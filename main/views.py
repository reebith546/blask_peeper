import logging

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

import requests

from catalog.models import Category, Product
from content.models import HomepageBlock
from delivery.models import ShopLocation
from delivery.services import QUOTE_NOTES, quote_delivery, suggest_addresses
from orders.models import Order, OrderItem
from payments import gateway
from reviews.models import Review

from .cart import Cart

logger = logging.getLogger('checkout')

# Поля, которые клиент задать НЕ может: стоимость доставки, зону и итоговую
# сумму считает исключительно сервер по адресу (quote_delivery). Значения
# этих полей из запроса не читаются нигде; список нужен только для того,
# чтобы зафиксировать в логах попытку их подсунуть.
_CLIENT_PRICING_FIELDS = (
    'delivery_price', 'delivery_zone', 'delivery_confirmed', 'total_price', 'items_total',
)


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
        # Все популярные товары — в карусель «Популярные сборки» (листается скроллом).
        'popular_products': (
            Product.objects
            .filter(is_popular=True, is_active=True, in_stock=True)
            .select_related('category')
            .order_by('-created_at')
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

    context = {
        'cart': cart,
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

        items_total = cart.get_total_price()
        details = cart.get_details()
        comment = request.POST.get('comment', '').strip()
        delivery_address = request.POST.get('delivery_address', '').strip()

        # Единственный источник стоимости доставки — расчёт на сервере по адресу.
        # Никаких зон/координат/цен от клиента не принимаем: поля формы,
        # относящиеся к цене доставки, игнорируются полностью.
        injected = [f for f in _CLIENT_PRICING_FIELDS if f in request.POST]
        if injected:
            logger.warning(
                'checkout: в запросе присутствуют поля ценообразования %s — '
                'игнорирую, стоимость доставки считает сервер (ip=%s)',
                ', '.join(injected), _checkout_client_ip(request),
            )
        delivery_zone, delivery_price, _distance_km, quote_state = quote_delivery(delivery_address)
        delivery_confirmed = quote_state == 'ok'
        if quote_state == 'on_request':
            comment = (
                f'ДОСТАВКА ПО СОГЛАСОВАНИЮ (зона «{delivery_zone.name}») — '
                f'связаться с клиентом, назвать стоимость.\n' + comment
            ).strip()
        elif not delivery_confirmed:
            comment = (
                f'СТОИМОСТЬ ДОСТАВКИ НЕ РАССЧИТАНА ({quote_state}) — согласовать с '
                f'клиентом перед подтверждением заказа.\n' + comment
            ).strip()

        order = Order.objects.create(
            # В оплату уводим только заказ с подтверждённой суммой доставки —
            # иначе не с чем идти в платёжный шлюз.
            status=(
                Order.Status.PENDING_PAYMENT
                if (gateway.payments_enabled() and delivery_confirmed)
                else Order.Status.NEW
            ),
            customer_name=request.POST.get('customer_name', '').strip(),
            customer_phone=request.POST.get('customer_phone', '').strip(),
            delivery_zone=delivery_zone,
            delivery_address=delivery_address,
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

        if not (gateway.payments_enabled() and delivery_confirmed):
            # Онлайн-оплата выключена ЛИБО стоимость доставки ещё не подтверждена —
            # заказ уходит менеджеру: он согласует сумму и (при необходимости)
            # пришлёт ссылку на оплату.
            return redirect('main:order_success', order_id=order.pk)

        # Онлайн-оплата: создаём счёт и уводим клиента на форму шлюза.
        payment = gateway.create_payment(order)
        try:
            form_url = gateway.init_payment(
                payment,
                success_url=request.build_absolute_uri(
                    reverse('main:order_success', args=[order.pk])),
                fail_url=request.build_absolute_uri(
                    reverse('payments:failed', args=[order.pk])),
            )
        except gateway.PaymentGatewayError:
            # Шлюз недоступен / отказал в создании счёта. Заказ не теряем и
            # клиента не пугаем «ошибкой оплаты»: оставляем как обычную заявку,
            # менеджер согласует оплату вручную.
            logger.exception('checkout: не удалось создать счёт для заказа %s', order.pk)
            order.status = Order.Status.NEW
            order.comment = (
                'ОНЛАЙН-ОПЛАТА НЕ ЗАПУСТИЛАСЬ (шлюз недоступен) — связаться с '
                'клиентом, принять оплату вручную / прислать ссылку.\n' + order.comment
            ).strip()
            order.save(update_fields=['status', 'comment', 'updated_at'])
            messages.info(
                request,
                'Заказ принят. Оплату согласует менеджер — мы свяжемся с вами.',
            )
            return redirect('main:order_success', order_id=order.pk)

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
    """Предпросмотр стоимости доставки по адресу — только для показа на чекауте.
    Не авторитетно: итоговую сумму считает checkout заново на сервере при
    отправке формы (quote_delivery). Координаты клиенту не отдаём."""
    address = request.GET.get('address', '').strip()
    zone, price, _distance_km, state = quote_delivery(address)
    return JsonResponse({
        'state': state,
        'confirmed': state == 'ok',
        'price': int(price) if state == 'ok' else None,
        'zone': zone.name if zone is not None else None,
        'delivery_time_minutes': zone.delivery_time_minutes if zone is not None else None,
        'note': QUOTE_NOTES.get(state, ''),
    })


def order_success(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, 'main/order_success.html', {'order': order})
