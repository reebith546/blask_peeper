import io
from decimal import Decimal

from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
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
            'delivery_zone': self.zone.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(response, 'подтвердите согласие')
        # Корзина не очищена — клиент может исправить и отправить повторно.
        self.assertEqual(len(self.client.get(reverse('main:cart')).context['cart']), 1)

    def test_full_checkout_creates_order_and_clears_cart(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self.client.post(reverse('main:cart_details'), {
            'delivery_date': '2026-08-20',
            'delivery_time': '14:00-16:00',
            'card_text': 'Поздравляю!',
        })
        response = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Анна',
            'customer_phone': '+77070000000',
            'legal_consent': 'yes',
            'delivery_address': 'ул. Тест, 1',
            'delivery_zone': self.zone.pk,
        })

        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get()
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.delivery_price, self.zone.price)
        self.assertEqual(order.total_price, self.product.price + self.zone.price)
        self.assertEqual(order.card_text, 'Поздравляю!')
        self.assertRedirects(response, reverse('main:order_success', args=[order.pk]))

        cart_response = self.client.get(reverse('main:cart'))
        self.assertContains(cart_response, 'Корзина пока пуста')

    def test_checkout_with_coordinates_resolves_zone_automatically(self):
        ShopLocation.objects.create(name='Магазин', latitude=Decimal('43.238949'), longitude=Decimal('76.889709'))
        # Точка в паре сотен метров от магазина — должна попасть в зону 0–5 км.
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        response = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Анна',
            'customer_phone': '+77070000000',
            'legal_consent': 'yes',
            'delivery_address': 'ул. Тест, 1',
            'delivery_lat': '43.240000',
            'delivery_lng': '76.891000',
        })
        order = Order.objects.get()
        self.assertEqual(order.delivery_zone, self.zone)
        self.assertEqual(order.delivery_price, self.zone.price)
        self.assertRedirects(response, reverse('main:order_success', args=[order.pk]))

    def test_checkout_with_coordinates_outside_all_zones_falls_back_to_manual_review(self):
        ShopLocation.objects.create(name='Магазин', latitude=Decimal('43.238949'), longitude=Decimal('76.889709'))
        # Точка в ~100 км от магазина — вне всех настроенных зон.
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self.client.post(reverse('main:checkout'), {
            'customer_name': 'Анна',
            'customer_phone': '+77070000000',
            'legal_consent': 'yes',
            'delivery_address': 'Далеко',
            'delivery_lat': '44.238949',
            'delivery_lng': '76.889709',
        })
        order = Order.objects.get()
        self.assertIsNone(order.delivery_zone)
        self.assertEqual(order.delivery_price, 0)
        self.assertIn('уточнить стоимость вручную', order.comment)

    def test_manual_zone_without_coordinates_is_flagged_for_staff_review(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        self.client.post(reverse('main:checkout'), {
            'customer_name': 'Анна',
            'customer_phone': '+77070000000',
            'legal_consent': 'yes',
            'delivery_address': 'ул. Тест, 1',
            'delivery_zone': self.zone.pk,
        })
        order = Order.objects.get()
        self.assertEqual(order.delivery_zone, self.zone)
        self.assertIn('без проверки адреса', order.comment)

    def test_checkout_ignores_tampered_manual_zone_when_coordinates_present(self):
        ShopLocation.objects.create(name='Магазин', latitude=Decimal('43.238949'), longitude=Decimal('76.889709'))
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        # Координаты соответствуют self.zone (0-5 км), но в форме подсунут id
        # far_zone (дешевле/дороже — тут важно, что он просто другой) — сервер
        # должен полностью проигнорировать это поле и посчитать зону сам.
        self.client.post(reverse('main:checkout'), {
            'customer_name': 'Анна',
            'customer_phone': '+77070000000',
            'legal_consent': 'yes',
            'delivery_address': 'ул. Тест, 1',
            'delivery_lat': '43.240000',
            'delivery_lng': '76.891000',
            'delivery_zone': self.far_zone.pk,
        })
        order = Order.objects.get()
        self.assertEqual(order.delivery_zone, self.zone)
        self.assertEqual(order.delivery_price, self.zone.price)
        self.assertNotEqual(order.delivery_zone, self.far_zone)


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
