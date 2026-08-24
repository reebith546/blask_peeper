from decimal import Decimal

from django.test import TestCase

from catalog.models import Category, Product

from .models import Order, OrderItem


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
