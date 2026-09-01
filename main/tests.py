import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import Category, Product
from delivery.models import DeliveryZone, ShopLocation
from orders.models import Order

from .cart import Cart


def _add_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _make_test_image():
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), '#BE9554').save(buf, format='JPEG')
    return SimpleUploadedFile('test.jpg', buf.getvalue(), content_type='image/jpeg')


class CartTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Категория')
        self.product = Product.objects.create(
            name='Букет', category=self.category, price=Decimal('1500'), in_stock=True,
        )
        self.request = _add_session(RequestFactory().get('/'))

    def test_add_increases_quantity_and_total(self):
        cart = Cart(self.request)
        cart.add(self.product, 2)
        self.assertEqual(len(cart), 2)
        self.assertEqual(cart.get_total_price(), Decimal('3000'))

    def test_add_is_clamped_to_max_quantity(self):
        from main.cart import MAX_QUANTITY_PER_ITEM

        cart = Cart(self.request)
        cart.add(self.product, MAX_QUANTITY_PER_ITEM + 10)
        self.assertEqual(len(cart), MAX_QUANTITY_PER_ITEM)

    def test_remove_empties_cart(self):
        cart = Cart(self.request)
        cart.add(self.product, 1)
        cart.remove(self.product)
        self.assertEqual(len(cart), 0)

    def test_set_quantity_below_one_removes_item(self):
        cart = Cart(self.request)
        cart.add(self.product, 1)
        cart.set_quantity(self.product, 0)
        self.assertEqual(len(cart), 0)


class CheckoutFlowTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Категория')
        self.product = Product.objects.create(
            name='Букет', category=self.category, price=Decimal('18500'), in_stock=True,
            image=_make_test_image(),
        )
        self.zone = DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )
        self.far_zone = DeliveryZone.objects.create(
            name='Дальний район', radius_from_km=5, radius_to_km=15, price=Decimal('3000'),
        )

    def test_add_to_cart_shows_up_in_cart_page(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 2})
        response = self.client.get(reverse('main:cart'))
        self.assertContains(response, self.product.name)
        self.assertContains(response, '37 000')

    def test_checkout_redirects_to_catalog_when_cart_empty(self):
        response = self.client.get(reverse('main:checkout'))
        self.assertRedirects(response, reverse('catalog:product_list'))

    def test_checkout_shows_legal_consent_checkbox(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        response = self.client.get(reverse('main:checkout'))
        self.assertContains(response, 'name="legal_consent" value="yes" required')
        self.assertContains(response, reverse('main:offer'))
        self.assertContains(response, reverse('main:privacy_policy'))

    def test_checkout_without_legal_consent_is_rejected(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        response = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Анна',
            'customer_phone': '+77070000000',
            'delivery_address': 'ул. Тест, 1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(response, 'подтвердите согласие')
        # Корзина не очищена — клиент может исправить и отправить повторно.
        self.assertEqual(len(self.client.get(reverse('main:cart')).context['cart']), 1)

    # ---- стоимость доставки: только сервер, клиент повлиять не может ----

    def _checkout(self, **extra):
        data = {
            'customer_name': 'Анна', 'customer_phone': '+77070000000',
            'legal_consent': 'yes', 'delivery_address': 'г. Алматы, ул. Абая, 10',
        }
        data.update(extra)
        return self.client.post(reverse('main:checkout'), data)

    @patch('main.views.quote_delivery')
    def test_full_checkout_uses_server_computed_delivery_price(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.2, 'ok')
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self.client.post(reverse('main:cart_details'), {
            'delivery_date': '2026-08-20', 'delivery_time': '14:00-16:00', 'card_text': 'Поздравляю!',
        })
        response = self._checkout()

        order = Order.objects.get()
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.delivery_zone, self.zone)
        self.assertEqual(order.delivery_price, self.zone.price)
        self.assertEqual(order.total_price, self.product.price + self.zone.price)
        self.assertEqual(order.card_text, 'Поздравляю!')
        self.assertRedirects(response, reverse('main:order_success', args=[order.pk]))
        self.assertContains(self.client.get(reverse('main:cart')), 'Корзина пока пуста')

    @patch('main.views.quote_delivery')
    def test_client_cannot_override_zone_or_price_via_form(self, quote):
        # Сервер посчитал дорогую зону, а клиент подсунул дешёвую + свою цену.
        quote.return_value = (self.far_zone, self.far_zone.price, 9.0, 'ok')
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self._checkout(
            delivery_zone=self.zone.pk,      # мусор — игнорируется
            delivery_price='1',              # мусор — игнорируется
            total_price='1',                 # мусор — игнорируется
        )
        order = Order.objects.get()
        self.assertEqual(order.delivery_zone, self.far_zone)
        self.assertEqual(order.delivery_price, self.far_zone.price)
        self.assertEqual(order.total_price, self.product.price + self.far_zone.price)

    @patch('main.views.quote_delivery')
    def test_out_of_zone_creates_request_without_price(self, quote):
        quote.return_value = (None, Decimal('0'), 140.0, 'out_of_zone')
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        response = self._checkout()
        order = Order.objects.get()
        self.assertIsNone(order.delivery_zone)
        self.assertEqual(order.delivery_price, Decimal('0'))
        self.assertEqual(order.total_price, self.product.price)
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertIn('НЕ РАССЧИТАНА', order.comment)
        self.assertIn('out_of_zone', order.comment)
        self.assertRedirects(response, reverse('main:order_success', args=[order.pk]))

    @override_settings(YANDEX_GEOCODER_API_KEY='', YANDEX_SUGGEST_API_KEY='')
    def test_maps_not_configured_creates_request(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self._checkout()
        order = Order.objects.get()
        self.assertEqual(order.delivery_price, Decimal('0'))
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertIn('maps_off', order.comment)

    @patch('main.views.quote_delivery')
    def test_geocode_failure_creates_request(self, quote):
        quote.return_value = (None, Decimal('0'), None, 'geocode_failed')
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self._checkout(delivery_address='кривой адрес')
        order = Order.objects.get()
        self.assertEqual(order.delivery_price, Decimal('0'))
        self.assertIn('geocode_failed', order.comment)

    @patch('main.views.quote_delivery')
    def test_on_request_zone_creates_request_and_keeps_zone_label(self, quote):
        far = DeliveryZone.objects.create(
            name='За городом', radius_from_km=100, radius_to_km=5000,
            price=None, price_on_request=True,
        )
        quote.return_value = (far, Decimal('0'), 150.0, 'on_request')
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        response = self._checkout()
        order = Order.objects.get()
        self.assertEqual(order.delivery_zone, far)          # менеджер видит зону
        self.assertEqual(order.delivery_price, Decimal('0'))
        self.assertEqual(order.total_price, self.product.price)
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertIn('ПО СОГЛАСОВАНИЮ', order.comment)
        self.assertIn('За городом', order.comment)
        self.assertRedirects(response, reverse('main:order_success', args=[order.pk]))


class DeliveryPriceTamperTests(TestCase):
    """Пробуем навязать серверу свою стоимость доставки всеми доступными способами.

    Ожидаемое поведение во всех случаях: цена доставки и итоговая сумма —
    ровно те, что вернул серверный расчёт quote_delivery; данные из запроса
    клиента на них не влияют.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Категория')
        self.product = Product.objects.create(
            name='Букет', category=self.category, price=Decimal('18500'),
            in_stock=True, image=_make_test_image(),
        )
        self.zone = DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('2500'),
        )
        # Зона с нулевой/несуществующей ценой — кандидат для подмены.
        self.on_request_zone = DeliveryZone.objects.create(
            name='Тест-подмена', radius_from_km=900, radius_to_km=1000,
            price=None, price_on_request=True,
        )

    def _fill_cart(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})

    def _post(self, **extra):
        data = {
            'customer_name': 'Малори', 'customer_phone': '+77070000000',
            'legal_consent': 'yes', 'delivery_address': 'г. Алматы, ул. Абая, 10',
        }
        data.update(extra)
        return self.client.post(reverse('main:checkout'), data)

    def _assert_server_price(self):
        order = Order.objects.get()
        self.assertEqual(order.delivery_zone, self.zone)
        self.assertEqual(order.delivery_price, Decimal('2500'))
        self.assertEqual(order.total_price, self.product.price + Decimal('2500'))
        return order

    @patch('main.views.quote_delivery')
    def test_post_delivery_price_field_is_ignored(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        for payload in ['0', '1', '-9999', '999999', '2500.01', 'abc', '', '2 500']:
            with self.subTest(payload=payload):
                Order.objects.all().delete()
                self._fill_cart()
                self._post(delivery_price=payload)
                self._assert_server_price()

    @patch('main.views.quote_delivery')
    def test_post_delivery_zone_pk_is_ignored(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        for payload in [str(self.on_request_zone.pk), '99999', 'abc', '']:
            with self.subTest(payload=payload):
                Order.objects.all().delete()
                self._fill_cart()
                self._post(delivery_zone=payload)
                self._assert_server_price()

    @patch('main.views.quote_delivery')
    def test_post_total_price_and_confirmed_flags_ignored(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        self._fill_cart()
        self._post(total_price='1', items_total='1', delivery_confirmed='true', delivery_price='1')
        self._assert_server_price()

    @patch('main.views.quote_delivery')
    def test_repeated_delivery_price_params_ignored(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        self._fill_cart()
        body = (
            'customer_name=A&customer_phone=%2B77070000000&legal_consent=yes'
            '&delivery_address=%D0%90%D0%B1%D0%B0%D1%8F+10'
            '&delivery_price=1&delivery_price=2&delivery_price=999999'
        )
        self.client.post(
            reverse('main:checkout'), body,
            content_type='application/x-www-form-urlencoded',
        )
        self._assert_server_price()

    @patch('main.views.quote_delivery')
    def test_json_request_body_cannot_set_price(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        self._fill_cart()
        # JSON-тело не попадает в request.POST: форма не заполнена, согласие
        # не пройдёт — заказ не создаётся вовсе, цену подсунуть не через что.
        self.client.post(
            reverse('main:checkout'),
            data='{"delivery_price": 1, "total_price": 1, "legal_consent": "yes"}',
            content_type='application/json',
        )
        self.assertEqual(Order.objects.count(), 0)

    def test_checkout_page_exposes_no_price_or_zone_controls(self):
        self._fill_cart()
        html = self.client.get(reverse('main:checkout')).content.decode()
        self.assertNotIn('name="delivery_price"', html)
        self.assertNotIn('name="delivery_zone"', html)
        self.assertNotIn('name="total_price"', html)
        self.assertNotIn('name="delivery_confirmed"', html)
        # Единственное скрытое поле — CSRF-токен, ни координат, ни зон в hidden нет.
        self.assertEqual(html.count('type="hidden"'), html.count('csrfmiddlewaretoken'))

    @patch('main.views.quote_delivery')
    def test_preview_endpoint_price_comes_from_server_not_query(self, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        resp = self.client.get(
            reverse('main:address_resolve'),
            {'address': 'Абая 10', 'price': '1', 'zone': '999', 'confirmed': 'false'},
        )
        data = resp.json()
        self.assertEqual(data['price'], 2500)
        self.assertEqual(data['zone'], 'Район')
        self.assertTrue(data['confirmed'])

    @patch('main.views.quote_delivery')
    def test_cheaper_looking_address_does_not_carry_a_client_price(self, quote):
        # Даже если клиент шлёт адрес + свою цену — берётся цена зоны из расчёта.
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        self._fill_cart()
        self._post(delivery_address='г. Алматы, ул. Абая, 10', delivery_price='0')
        self._assert_server_price()


class PageSmokeTests(TestCase):
    def test_home_page_loads(self):
        self.assertEqual(self.client.get(reverse('main:home')).status_code, 200)

    def test_home_shows_all_popular_products(self):
        category = Category.objects.create(name='Категория')
        made = []
        for i in range(7):
            made.append(Product.objects.create(
                name=f'Популярный {i}', category=category, price=1000,
                in_stock=True, is_active=True, is_popular=True, image=_make_test_image(),
            ))
        # непопулярный / не в наличии — в карусель не попадают
        Product.objects.create(name='Обычный', category=category, price=1000,
                               in_stock=True, is_popular=False, image=_make_test_image())
        Product.objects.create(name='Нет в наличии', category=category, price=1000,
                               in_stock=False, is_popular=True, image=_make_test_image())

        response = self.client.get(reverse('main:home'))
        shown = list(response.context['popular_products'])
        self.assertEqual({p.pk for p in shown}, {p.pk for p in made})
        for p in made:
            self.assertContains(response, p.get_absolute_url() if hasattr(p, 'get_absolute_url')
                                else reverse('catalog:product_detail', args=[p.slug]))

    def test_about_page_loads(self):
        self.assertEqual(self.client.get(reverse('main:about')).status_code, 200)

    def test_offer_page_loads(self):
        response = self.client.get(reverse('main:offer'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Публичная')

    def test_privacy_policy_page_loads(self):
        response = self.client.get(reverse('main:privacy_policy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'персональных данных')

    def test_internal_page_headers_share_the_hero_banner_image(self):
        from content.models import HomepageBlock

        block = HomepageBlock.objects.create(
            block_type=HomepageBlock.BlockType.HERO, is_active=True,
            image=_make_test_image(), order=0,
        )
        Category.objects.create(name='Авторские', slug='avtorskie')

        for name, args in [
            ('catalog:product_list', []),
            ('catalog:product_list_by_category', ['avtorskie']),
            ('main:about', []),
            ('main:offer', []),
            ('main:privacy_policy', []),
        ]:
            with self.subTest(page=name):
                html = self.client.get(reverse(name, args=args)).content.decode()
                self.assertIn('page-hero--image', html)
                self.assertIn(block.image.url, html)

    def test_internal_page_headers_stay_plain_without_a_hero_block(self):
        html = self.client.get(reverse('main:about')).content.decode()
        self.assertIn('class="page-hero"', html)
        self.assertNotIn('page-hero--image', html)

    def test_category_page_uses_vertical_grid_not_carousel(self):
        category = Category.objects.create(name='Авторские')
        Product.objects.create(name='Букет', category=category, price=1000,
                               in_stock=True, image=_make_test_image())
        response = self.client.get(
            reverse('catalog:product_list_by_category', args=[category.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="bouquets-grid"')
        self.assertNotContains(response, 'class="carousel"')
        self.assertNotContains(response, 'class="carousel__track')

    def test_catalog_and_product_detail_load(self):
        category = Category.objects.create(name='Категория')
        product = Product.objects.create(
            name='Букет', category=category, price=1000, in_stock=True, image=_make_test_image(),
        )
        self.assertEqual(self.client.get(reverse('catalog:product_list')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('catalog:product_detail', args=[product.slug])).status_code, 200,
        )


class AdminDashboardTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.superuser = User.objects.create_superuser('owner', 'owner@example.com', 'pass12345')

    def test_dashboard_shows_only_nonzero_attention_stats(self):
        from reviews.models import Review

        category = Category.objects.create(name='Категория')
        Product.objects.create(
            name='Букет', category=category, price=1000, in_stock=False, image=_make_test_image(),
        )
        Order.objects.create(
            customer_name='Анна', customer_phone='+77070000000', delivery_address='ул. Тест, 1',
        )
        Review.objects.create(author_name='Клиент', text='Отзыв', status=Review.Status.DRAFT)

        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:index'))

        self.assertEqual(response.status_code, 200)
        stats = {stat['label']: stat['value'] for stat in response.context['dashboard_stats']}
        self.assertEqual(stats['Новых заказов'], 1)
        self.assertEqual(stats['Товаров без остатка'], 1)
        self.assertEqual(stats['Отзывов на модерации'], 1)
        # Нулевые показатели на дашборд не выводятся.
        self.assertNotIn('Неудачных входов за сутки', stats)
        self.assertFalse(response.context['dashboard_calm'])

    def test_dashboard_is_calm_when_nothing_needs_attention(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.context['dashboard_stats'], [])
        self.assertTrue(response.context['dashboard_calm'])
