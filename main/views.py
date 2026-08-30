from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

import requests

from catalog.models import Category, Product
from content.models import HomepageBlock
from delivery.models import DeliveryZone, ShopLocation
from delivery.services import geocode_address, geocode_uri, resolve_delivery_zone, suggest_addresses
from orders.models import Order, OrderItem
from payments import gateway
from reviews.models import Review

from .cart import Cart


def _checkout_client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def about(request):
    return render(request, 'main/about.html')


def offer(request):
    """Публичный договор-оферта купли-продажи и доставки цветочной продукции."""
    return render(request, 'main/legal_offer.html')


def privacy_policy(request):
    """Политика конфиденциальности и обработки персональных данных."""
    return render(request, 'main/legal_privacy.html')


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
            .filter(is_popular=True, is_active=True, in_stock=True)
            .select_related('category')[:4]
        ),
        'categories': Category.objects.filter(is_active=True, show_on_homepage=True).order_by('order'),
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
    product = get_object_or_404(Product, pk=product_id, is_active=True, in_stock=True)
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
    context = {
        'cart': cart,
        'delivery_zones': delivery_zones,
        'details': cart.get_details(),
        'yandex_enabled': bool(settings.YANDEX_SUGGEST_API_KEY and settings.YANDEX_GEOCODER_API_KEY),
        'payments_enabled': gateway.payments_enabled(),
    }

    if request.method == 'POST':
        # Согласие с офертой и политикой обязательно. На клиенте это гарантирует
        # атрибут required у чекбокса, здесь — страховка от обхода валидации.
        if not request.POST.get('legal_consent'):
            messages.error(
                request,
                'Чтобы оформить заказ, подтвердите согласие с публичной офертой '
                'и политикой конфиденциальности.',
            )
            return render(request, 'main/checkout.html', context)

        # При онлайн-оплате email обязателен — его требует платёжный шлюз.
        customer_email = request.POST.get('customer_email', '').strip()
        if gateway.payments_enabled() and not customer_email:
            messages.error(request, 'Укажите email — на него придёт чек об оплате.')
            return render(request, 'main/checkout.html', context)

        items_total = cart.get_total_price()
        details = cart.get_details()
        comment = request.POST.get('comment', '').strip()

        # Координаты используются только на лету — сохранять их в БД нельзя
        # по условиям бесплатного тарифа Яндекс Карт (см. комментарий в модели Order).
        delivery_lat = request.POST.get('delivery_lat', '').strip()
        delivery_lng = request.POST.get('delivery_lng', '').strip()

        if delivery_lat and delivery_lng:
            # Координаты пришли с виджета подсказок адреса (Яндекс Карты) — это
            # авторитетный источник цены, ручной выбор зоны игнорируется.
            delivery_zone, _distance_km = resolve_delivery_zone(delivery_lat, delivery_lng)
            if delivery_zone is None:
                comment = (
                    'Автоматический расчёт доставки недоступен (адрес вне зоны доставки '
                    'или не настроена точка магазина) — уточнить стоимость вручную.\n' + comment
                ).strip()
        else:
            # Координаты не пришли (виджет недоступен или клиент не выбрал
            # подсказку) — доверяем ручному выбору зоны, но помечаем заказ,
            # чтобы менеджер сверил адрес и стоимость перед подтверждением.
            zone_id = request.POST.get('delivery_zone')
            delivery_zone = delivery_zones.filter(pk=zone_id).first()
            comment = (
                'Зона доставки выбрана вручную, без проверки адреса по карте — '
                'сверить с клиентом при подтверждении заказа.\n' + comment
            ).strip()

        delivery_price = delivery_zone.price if delivery_zone else 0

        order = Order.objects.create(
            status=(
                Order.Status.PENDING_PAYMENT if gateway.payments_enabled()
                else Order.Status.NEW
            ),
            customer_name=request.POST.get('customer_name', '').strip(),
            customer_phone=request.POST.get('customer_phone', '').strip(),
            customer_email=customer_email,
            delivery_zone=delivery_zone,
            delivery_address=request.POST.get('delivery_address', '').strip(),
            delivery_date=details.get('delivery_date') or None,
            delivery_time=details.get('delivery_time', ''),
            delivery_price=delivery_price,
            card_text=details.get('card_text', ''),
            comment=comment,
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

        if not gateway.payments_enabled():
            # Онлайн-оплата не подключена — менеджер свяжется и примет оплату.
            return redirect('main:order_success', order_id=order.pk)

        # Онлайн-оплата: создаём платёж и уводим клиента на форму шлюза.
        payment = gateway.create_payment(order)
        try:
            form_url = gateway.init_payment(
                payment,
                client_ip=_checkout_client_ip(request),
                success_url=request.build_absolute_uri(
                    reverse('main:order_success', args=[order.pk])),
                fail_url=request.build_absolute_uri(
                    reverse('payments:failed', args=[order.pk])),
                callback_url=request.build_absolute_uri(
                    reverse('payments:callback')),
            )
        except gateway.PaymentGatewayError:
            order.status = Order.Status.PAYMENT_FAILED
            order.save(update_fields=['status', 'updated_at'])
            messages.error(request, 'Не удалось начать оплату. Попробуйте ещё раз.')
            return redirect('payments:failed', order_id=order.pk)

        return redirect(form_url)

    return render(request, 'main/checkout.html', context)


def address_suggest_ajax(request):
    """Подсказки адреса для чекаута — проксирует Яндекс Геосаджест с сервера."""
    query = request.GET.get('q', '').strip()
    if len(query) < 3:
        return JsonResponse({'results': []})

    shop = ShopLocation.objects.first()
    try:
        results = suggest_addresses(
            query,
            bias_latitude=shop.latitude if shop else None,
            bias_longitude=shop.longitude if shop else None,
        )
    except requests.RequestException:
        results = []

    simplified = []
    for item in results:
        address = item.get('address', {}).get('formatted_address', '')
        uri = item.get('uri', '')
        # Дома геокодируются по строке адреса (у них нет uri), организации — по uri.
        if not address and not uri:
            continue
        subtitle = item.get('subtitle', {}).get('text', '')
        title = item.get('title', {}).get('text', '')
        simplified.append({
            'label': f'{title}, {subtitle}' if subtitle else title,
            'address': address,
            'uri': uri,
        })
    return JsonResponse({'results': simplified})


def address_resolve_ajax(request):
    """По адресу/uri подсказки — геокодирует и подбирает зону/цену. Ничего не сохраняет в БД."""
    address = request.GET.get('address', '').strip()
    uri = request.GET.get('uri', '').strip()
    if not address and not uri:
        return JsonResponse({'zone': None}, status=400)

    try:
        lat, lng = geocode_address(address) if address else geocode_uri(uri)
    except requests.RequestException:
        lat = lng = None

    if lat is None or lng is None:
        return JsonResponse({'zone': None, 'lat': None, 'lng': None})

    zone, _distance_km = resolve_delivery_zone(lat, lng)
    payload = {'lat': lat, 'lng': lng, 'zone': None}
    if zone is not None:
        payload['zone'] = {
            'id': zone.pk,
            'name': zone.name,
            'price': int(zone.price),
            'delivery_time_minutes': zone.delivery_time_minutes,
        }
    return JsonResponse(payload)


def order_success(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, 'main/order_success.html', {'order': order})
