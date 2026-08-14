from decimal import Decimal

from catalog.models import Product

CART_SESSION_KEY = 'cart'
DETAILS_SESSION_KEY = 'cart_details'


class Cart:
    """Корзина покупателя на сессии — работает без авторизации."""

    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(CART_SESSION_KEY)
        if cart is None:
            cart = self.session[CART_SESSION_KEY] = {}
        self.cart = cart

    def add(self, product, quantity=1):
        product_id = str(product.pk)
        current = self.cart.get(product_id, {}).get('quantity', 0)
        quantity = max(1, current + quantity)
        if product.stock:
            quantity = min(quantity, product.stock)
        self.cart[product_id] = {'quantity': quantity}
        self._save()

    def set_quantity(self, product, quantity):
        product_id = str(product.pk)
        if quantity < 1:
            self.remove(product)
            return
        if product.stock:
            quantity = min(quantity, product.stock)
        self.cart[product_id] = {'quantity': quantity}
        self._save()

    def remove(self, product):
        product_id = str(product.pk)
        if product_id in self.cart:
            del self.cart[product_id]
            self._save()

    def clear(self):
        self.session[CART_SESSION_KEY] = {}
        self.session[DETAILS_SESSION_KEY] = {}
        self._save()

    def get_details(self):
        return self.session.get(DETAILS_SESSION_KEY, {})

    def set_details(self, delivery_date='', delivery_time='', card_text=''):
        self.session[DETAILS_SESSION_KEY] = {
            'delivery_date': delivery_date,
            'delivery_time': delivery_time,
            'card_text': card_text,
        }
        self._save()

    def _save(self):
        self.session.modified = True

    def __iter__(self):
        products = Product.objects.filter(pk__in=self.cart.keys())
        products_map = {str(product.pk): product for product in products}
        for product_id, item in self.cart.items():
            product = products_map.get(product_id)
            if product is None:
                continue
            quantity = item['quantity']
            yield {
                'product': product,
                'quantity': quantity,
                'price': product.price,
                'subtotal': product.price * quantity,
            }

    def __len__(self):
        return sum(item['quantity'] for item in self.cart.values())

    def get_total_price(self):
        return sum((entry['subtotal'] for entry in self), Decimal('0'))
