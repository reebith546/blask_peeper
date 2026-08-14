import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from delivery.models import ShopLocation
from delivery.services import YANDEX_GEOCODER_URL, YANDEX_SUGGEST_URL


class Command(BaseCommand):
    help = (
        'Диагностика интеграции с Яндекс Картами: проверяет оба ключа '
        '(Геосаджест и Геокодер) прямыми запросами и печатает подробный '
        'результат — статус-код, тело ответа, где не хватает данных.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--query', default='Алматы, Абая 10',
            help='Тестовый адрес для проверки подсказок (по умолчанию: "Алматы, Абая 10")',
        )

    def handle(self, *args, **options):
        query = options['query']
        ok = True

        self.stdout.write('=== Ключи из .env ===')
        suggest_key = settings.YANDEX_SUGGEST_API_KEY
        geocoder_key = settings.YANDEX_GEOCODER_API_KEY
        self._print_key_status('YANDEX_SUGGEST_API_KEY', suggest_key)
        self._print_key_status('YANDEX_GEOCODER_API_KEY', geocoder_key)
        if not suggest_key or not geocoder_key:
            self.stderr.write(self.style.ERROR(
                'Один или оба ключа не заданы в .env — дальше проверять нечего.'
            ))
            return

        self.stdout.write('')
        self.stdout.write(f'=== 1. Геосаджест: подсказки по запросу "{query}" ===')
        suggest_ok, first_uri = self._test_suggest(suggest_key, query)
        ok = ok and suggest_ok

        self.stdout.write('')
        self.stdout.write('=== 2. Геокодер: координаты по тексту (без uri) ===')
        geocode_ok = self._test_geocode_by_text(geocoder_key, query)
        ok = ok and geocode_ok

        if first_uri:
            self.stdout.write('')
            self.stdout.write('=== 3. Геокодер: координаты по uri из Геосаджеста ===')
            self._test_geocode_by_uri(geocoder_key, first_uri)

        self.stdout.write('')
        self.stdout.write('=== 4. Точка магазина (ShopLocation) ===')
        shop = ShopLocation.objects.first()
        if shop:
            self.stdout.write(self.style.SUCCESS(
                f'Настроена: {shop.name} ({shop.latitude}, {shop.longitude})'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                'Не настроена — без неё подсказки не смещаются к Алматы и зона '
                'доставки не считается. Добавьте в /admin/delivery/shoplocation/'
            ))

        self.stdout.write('')
        if ok:
            self.stdout.write(self.style.SUCCESS('Итог: оба ключа рабочие.'))
        else:
            self.stdout.write(self.style.ERROR(
                'Итог: минимум один запрос упал. Смотрите статус-коды и тела '
                'ответов выше — 403 "Invalid api key" означает, что сам Яндекс '
                'не принимает ключ (не наш код), несмотря на статус "Активен" '
                'в кабинете. Возможные причины: ограничение по IP/региону '
                'запроса, аккаунт требует подтверждения телефона, ключ стоит '
                'пересоздать.'
            ))

    def _print_key_status(self, name, value):
        if not value:
            self.stdout.write(self.style.ERROR(f'{name}: не задан'))
        else:
            masked = value[:8] + '...' + value[-4:] if len(value) > 12 else value
            self.stdout.write(f'{name}: {masked}')

    def _test_suggest(self, key, query):
        params = {
            'apikey': key, 'text': query, 'results': 3,
            'print_address': 1, 'lang': 'ru_RU',
        }
        try:
            response = requests.get(YANDEX_SUGGEST_URL, params=params, timeout=5)
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f'Сетевая ошибка: {exc}'))
            return False, None

        self.stdout.write(f'HTTP {response.status_code}')
        self.stdout.write(f'Тело ответа: {response.text[:300]}')
        if response.status_code != 200:
            self.stdout.write(self.style.ERROR('Геосаджест не сработал.'))
            return False, None

        results = response.json().get('results', [])
        self.stdout.write(self.style.SUCCESS(f'OK, найдено подсказок: {len(results)}'))
        for item in results[:3]:
            title = item.get('title', {}).get('text', '')
            self.stdout.write(f'  - {title}')
        first_uri = results[0]['uri'] if results and results[0].get('uri') else None
        return True, first_uri

    def _test_geocode_by_text(self, key, query):
        params = {'apikey': key, 'format': 'json', 'geocode': query}
        try:
            response = requests.get(YANDEX_GEOCODER_URL, params=params, timeout=5)
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f'Сетевая ошибка: {exc}'))
            return False

        self.stdout.write(f'HTTP {response.status_code}')
        self.stdout.write(f'Тело ответа: {response.text[:300]}')
        if response.status_code != 200:
            self.stdout.write(self.style.ERROR('Геокодер не сработал.'))
            return False

        self.stdout.write(self.style.SUCCESS('OK'))
        return True

    def _test_geocode_by_uri(self, key, uri):
        params = {'apikey': key, 'format': 'json', 'uri': uri}
        try:
            response = requests.get(YANDEX_GEOCODER_URL, params=params, timeout=5)
        except requests.RequestException as exc:
            self.stderr.write(self.style.ERROR(f'Сетевая ошибка: {exc}'))
            return

        self.stdout.write(f'HTTP {response.status_code}')
        self.stdout.write(f'Тело ответа: {response.text[:300]}')
        if response.status_code == 200:
            self.stdout.write(self.style.SUCCESS('OK'))
        else:
            self.stdout.write(self.style.ERROR('Геокодер по uri не сработал.'))
