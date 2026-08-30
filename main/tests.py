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


class PageSmokeTests(TestCase):
    def test_home_page_loads(self):
        self.assertEqual(self.client.get(reverse('main:home')).status_code, 200)

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
