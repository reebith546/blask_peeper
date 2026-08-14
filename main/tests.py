import io
from decimal import Decimal

from django.contrib.auth.models import Group
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from PIL import Image

from catalog.models import Category, Product
from content.models import NewsletterSubscriber
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
            name='Букет', category=self.category, price=Decimal('1500'), stock=2,
        )
        self.request = _add_session(RequestFactory().get('/'))

    def test_add_increases_quantity_and_total(self):
        cart = Cart(self.request)
        cart.add(self.product, 2)
        self.assertEqual(len(cart), 2)
        self.assertEqual(cart.get_total_price(), Decimal('3000'))

    def test_add_is_clamped_to_available_stock(self):
        cart = Cart(self.request)
        cart.add(self.product, 5)
        self.assertEqual(len(cart), 2)

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
            name='Букет', category=self.category, price=Decimal('18500'), stock=5,
            image=_make_test_image(),
        )
        self.zone = DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )

    def test_add_to_cart_shows_up_in_cart_page(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 2})
        response = self.client.get(reverse('main:cart'))
        self.assertContains(response, self.product.name)
        self.assertContains(response, '37 000')

    def test_checkout_redirects_to_catalog_when_cart_empty(self):
        response = self.client.get(reverse('main:checkout'))
        self.assertRedirects(response, reverse('catalog:product_list'))

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
            'delivery_address': 'Далеко',
            'delivery_lat': '44.238949',
            'delivery_lng': '76.889709',
        })
        order = Order.objects.get()
        self.assertIsNone(order.delivery_zone)
        self.assertEqual(order.delivery_price, 0)
        self.assertIn('уточнить стоимость вручную', order.comment)


class NewsletterTests(TestCase):
    def test_valid_email_creates_subscriber(self):
        self.client.post(reverse('main:newsletter_subscribe'), {'email': 'test@example.com'})
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)

    def test_invalid_email_is_rejected(self):
        self.client.post(reverse('main:newsletter_subscribe'), {'email': 'not-an-email'})
        self.assertEqual(NewsletterSubscriber.objects.count(), 0)


class PageSmokeTests(TestCase):
    def test_home_page_loads(self):
        self.assertEqual(self.client.get(reverse('main:home')).status_code, 200)

    def test_about_page_loads(self):
        self.assertEqual(self.client.get(reverse('main:about')).status_code, 200)

    def test_catalog_and_product_detail_load(self):
        category = Category.objects.create(name='Категория')
        product = Product.objects.create(
            name='Букет', category=category, price=1000, stock=1, image=_make_test_image(),
        )
        self.assertEqual(self.client.get(reverse('catalog:product_list')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('catalog:product_detail', args=[product.slug])).status_code, 200,
        )


class SetupRolesCommandTests(TestCase):
    def test_manager_group_excludes_delivery_and_payments(self):
        call_command('setup_roles')
        group = Group.objects.get(name='Менеджер магазина')
        apps = set(group.permissions.values_list('content_type__app_label', flat=True))
        self.assertEqual(apps, {'catalog', 'orders', 'content', 'reviews'})
        codenames = group.permissions.values_list('codename', flat=True)
        self.assertFalse(any(c.startswith('delete_') for c in codenames))
