from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Category, Product
from content.models import HomepageBlock, NewsletterSubscriber
from delivery.models import DeliveryZone
from orders.models import Order, OrderItem
from reviews.models import Review

from .cart import Cart


def about(request):
    return render(request, 'main/about.html')


@require_POST
def newsletter_subscribe(request):
    email = request.POST.get('email', '').strip()
    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, 'Введите корректный email')
    else:
        NewsletterSubscriber.objects.get_or_create(email=email)
        messages.success(request, 'Вы подписаны на новости')
    return redirect(request.POST.get('next') or 'main:home')


def home(request):
    context = {
        'hero_block': (
            HomepageBlock.objects
            .filter(block_type=HomepageBlock.BlockType.HERO, is_active=True)
            .order_by('order')
            .first()
        ),
        'popular_products': (
            Product.objects
            .filter(is_popular=True, is_active=True)
            .select_related('category')[:4]
        ),
        'categories': Category.objects.filter(is_active=True).order_by('order')[:6],
        'instagram_blocks': (
            HomepageBlock.objects
            .filter(block_type=HomepageBlock.BlockType.INSTAGRAM, is_active=True)
            .order_by('order')[:5]
        ),
        'reviews': (
            Review.objects
            .filter(status=Review.Status.PUBLISHED)
            .order_by('-created_at')[:4]
        ),
    }
    return render(request, 'main/home.html', context)


def cart_detail(request):
    return render(request, 'main/cart.html')


@require_POST
def cart_add(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    try:
        quantity = int(request.POST.get('quantity', 1))
    except ValueError:
        quantity = 1
    cart.add(product, quantity)
    messages.success(request, f'«{product.name}» добавлен в корзину')
    next_url = request.POST.get('next')
    return redirect(next_url or 'main:cart')


@require_POST
def cart_update(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, pk=product_id)
    try:
        quantity = int(request.POST.get('quantity', 1))
    except ValueError:
        quantity = 1
    cart.set_quantity(product, quantity)
    return redirect('main:cart')


@require_POST
def cart_remove(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, pk=product_id)
    cart.remove(product)
    return redirect('main:cart')


@require_POST
def cart_details(request):
    cart = Cart(request)
    if len(cart) == 0:
        return redirect('main:cart')
    cart.set_details(
        delivery_date=request.POST.get('delivery_date', ''),
        delivery_time=request.POST.get('delivery_time', ''),
        card_text=request.POST.get('card_text', ''),
    )
    return redirect('main:checkout')


def checkout(request):
    cart = Cart(request)
    if len(cart) == 0:
        return redirect('catalog:product_list')

    delivery_zones = DeliveryZone.objects.filter(is_active=True).order_by('order')

    if request.method == 'POST':
        zone_id = request.POST.get('delivery_zone')
        delivery_zone = delivery_zones.filter(pk=zone_id).first()
        delivery_price = delivery_zone.price if delivery_zone else 0
        items_total = cart.get_total_price()
        details = cart.get_details()

        order = Order.objects.create(
            customer_name=request.POST.get('customer_name', '').strip(),
            customer_phone=request.POST.get('customer_phone', '').strip(),
            customer_email=request.POST.get('customer_email', '').strip(),
            delivery_zone=delivery_zone,
            delivery_address=request.POST.get('delivery_address', '').strip(),
            delivery_date=details.get('delivery_date') or None,
            delivery_time=details.get('delivery_time', ''),
            delivery_price=delivery_price,
            card_text=details.get('card_text', ''),
            comment=request.POST.get('comment', '').strip(),
            total_price=items_total + delivery_price,
        )
        for entry in cart:
            OrderItem.objects.create(
                order=order,
                product=entry['product'],
                quantity=entry['quantity'],
                price=entry['price'],
            )
        cart.clear()
        return redirect('main:order_success', order_id=order.pk)

    context = {
        'cart': cart,
        'delivery_zones': delivery_zones,
        'details': cart.get_details(),
    }
    return render(request, 'main/checkout.html', context)


def order_success(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, 'main/order_success.html', {'order': order})
