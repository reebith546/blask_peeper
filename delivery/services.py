import math
from decimal import Decimal

import requests
from django.conf import settings

from .models import DeliveryZone, ShopLocation

EARTH_RADIUS_KM = 6371.0

# Понятные пояснения для каждого исхода расчёта доставки.
QUOTE_NOTES = {
    'ok': '',
    'maps_off': 'Стоимость доставки подтвердит менеджер после оформления.',
    'no_shop': 'Стоимость доставки подтвердит менеджер после оформления.',
    'geocode_failed': 'Адрес не распознан — стоимость доставки подтвердит менеджер.',
    'out_of_zone': 'Адрес за пределами зон доставки — стоимость подтвердит менеджер.',
}

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


def quote_delivery(address_text):
    """Единственный авторитетный расчёт стоимости доставки — целиком на сервере,
    по строке адреса. Клиент на цену повлиять не может (ни зоны, ни координат
    от клиента не принимаем).

    Возвращает (zone|None, price: Decimal, distance_km|None, state), где state:
      ok             — адрес распознан и попал в активную зону, price = цена зоны
      maps_off       — не настроен ключ Геокодера
      no_shop        — не задана точка магазина
      geocode_failed — пустой адрес / Яндекс недоступен / адрес не найден
      out_of_zone    — адрес распознан, но расстояние вне всех активных зон
    Во всех состояниях кроме ok price == 0 и стоимость доставки уточняет менеджер.
    """
    zero = Decimal('0')
    address_text = (address_text or '').strip()
    if not address_text:
        return None, zero, None, 'geocode_failed'
    if not settings.YANDEX_GEOCODER_API_KEY:
        return None, zero, None, 'maps_off'
    if ShopLocation.objects.first() is None:
        return None, zero, None, 'no_shop'

    try:
        latitude, longitude = geocode_address(address_text)
    except requests.RequestException:
        latitude = longitude = None
    if latitude is None or longitude is None:
        return None, zero, None, 'geocode_failed'

    zone, distance_km = resolve_delivery_zone(latitude, longitude)
    if zone is None:
        return None, zero, distance_km, 'out_of_zone'
    return zone, zone.price, distance_km, 'ok'


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
