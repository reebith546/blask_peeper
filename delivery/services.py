import math

import requests
from django.conf import settings

from .models import DeliveryZone, ShopLocation

EARTH_RADIUS_KM = 6371.0

YANDEX_SUGGEST_URL = 'https://suggest-maps.yandex.ru/v1/suggest'
YANDEX_GEOCODER_URL = 'https://geocode-maps.yandex.ru/1.x/'


def haversine_km(lat1, lon1, lat2, lon2):
    """Расстояние по прямой между двумя точками на сфере, в километрах."""
    lat1, lon1, lat2, lon2 = (math.radians(float(v)) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def resolve_delivery_zone(latitude, longitude):
    """Подбирает DeliveryZone по координатам клиента.

    Возвращает (zone, distance_km). zone is None, если магазин не настроен
    или расстояние не попало ни в одну активную зону (клиент вне зоны
    доставки) — в обоих случаях цену доставки должен уточнить менеджер.
    """
    shop = ShopLocation.objects.first()
    if shop is None:
        return None, None

    distance_km = haversine_km(shop.latitude, shop.longitude, latitude, longitude)
    zone = (
        DeliveryZone.objects
        .filter(is_active=True, radius_from_km__lte=distance_km, radius_to_km__gt=distance_km)
        .order_by('radius_from_km')
        .first()
    )
    return zone, round(distance_km, 2)


def suggest_addresses(query, bias_latitude=None, bias_longitude=None):
    """Подсказки адреса через Яндекс Геосаджест.

    Запрос идёт с сервера, не из браузера — у этого API нет CORS для
    прямых вызовов с фронта. Возвращает список подсказок (без координат,
    их отдаёт только Геокодер).
    """
    params = {
        'apikey': settings.YANDEX_SUGGEST_API_KEY,
        'text': query,
        'results': 5,
        'print_address': 1,
        'lang': 'ru_RU',
    }
    if bias_latitude and bias_longitude:
        params['ll'] = f'{bias_longitude},{bias_latitude}'
        params['spn'] = '1,1'

    response = requests.get(YANDEX_SUGGEST_URL, params=params, timeout=3)
    response.raise_for_status()
    return response.json().get('results', [])


def _geocode(params):
    params = {'apikey': settings.YANDEX_GEOCODER_API_KEY, 'format': 'json', **params}
    response = requests.get(YANDEX_GEOCODER_URL, params=params, timeout=3)
    response.raise_for_status()
    members = response.json()['response']['GeoObjectCollection']['featureMember']
    if not members:
        return None, None

    longitude, latitude = members[0]['GeoObject']['Point']['pos'].split(' ')
    return float(latitude), float(longitude)


def geocode_address(address_text):
    """Координаты по обычному тексту адреса («Алматы, Абая 10»).

    Основной способ — у подсказок Геосаджеста для домов (tags: ["house"])
    поле uri в ответе не приходит (несмотря на документацию), только
    у организаций. Для доставки нам нужны именно дома, так что геокодируем
    напрямую по строке адреса.
    """
    return _geocode({'geocode': address_text})


def geocode_uri(uri):
    """Координаты подсказки через Яндекс Геокодер по её uri (для организаций).

    Возвращает (latitude, longitude) либо (None, None), если объект не найден.
    """
    return _geocode({'uri': uri})
