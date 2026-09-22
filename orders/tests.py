import io
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from catalog.models import Category, Product

from .models import Order, OrderItem


def _make_test_image():
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), '#BE9554').save(buf, format='JPEG')
    return SimpleUploadedFile('test.jpg', buf.getvalue(), content_type='image/jpeg')


class OrderItemModelTests(TestCase):
    def test_subtotal_is_price_times_quantity(self):
        category = Category.objects.create(name='Категория')
        product = Product.objects.create(name='Букет', category=category, price=Decimal('18500'), in_stock=True)
        order = Order.objects.create(customer_name='Анна', customer_phone='+77070000000', delivery_address='ул. Тест, 1')
        item = OrderItem.objects.create(order=order, product=product, quantity=3, price=product.price)
        self.assertEqual(item.subtotal, Decimal('55500'))


class OrderModelTests(TestCase):
    def test_default_status_is_new(self):
        order = Order.objects.create(customer_name='Анна', customer_phone='+77070000000', delivery_address='ул. Тест, 1')
        self.assertEqual(order.status, Order.Status.NEW)


class OrderAdminBouquetPhotoTests(TestCase):
    """В карточке заказа у каждой позиции должно быть фото её букета —
    одно фото на позицию, сколько букетов, столько и фото."""

    def setUp(self):
        self.superuser = User.objects.create_superuser('owner', 'owner@example.com', 'pass12345')
        self.category = Category.objects.create(name='Категория')

    def test_order_change_page_shows_one_photo_per_item(self):
        rose = Product.objects.create(
            name='Розы', category=self.category, price=Decimal('15000'), image=_make_test_image(),
        )
        tulip = Product.objects.create(
            name='Тюльпаны', category=self.category, price=Decimal('5000'), image=_make_test_image(),
        )
        order = Order.objects.create(
            customer_name='Анна', customer_phone='+77070000000', delivery_address='ул. Тест, 1',
        )
        OrderItem.objects.create(order=order, product=rose, quantity=1, price=rose.price)
        OrderItem.objects.create(order=order, product=tulip, quantity=2, price=tulip.price)

        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:orders_order_change', args=[order.pk]))
        html = response.content.decode()
        self.assertEqual(html.count('<img src="/media/products/'), 2)
        self.assertIn(rose.image.url, html)
        self.assertIn(tulip.image.url, html)

    def test_single_bouquet_order_shows_exactly_one_photo(self):
        rose = Product.objects.create(
            name='Розы', category=self.category, price=Decimal('15000'), image=_make_test_image(),
        )
        order = Order.objects.create(
            customer_name='Анна', customer_phone='+77070000000', delivery_address='ул. Тест, 1',
        )
        OrderItem.objects.create(order=order, product=rose, quantity=1, price=rose.price)

        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:orders_order_change', args=[order.pk]))
        self.assertEqual(response.content.decode().count('<img src="/media/products/'), 1)
