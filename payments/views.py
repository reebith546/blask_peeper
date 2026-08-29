import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from orders.models import Order

from . import gateway
from .models import Payment

logger = logging.getLogger('payments')


def _client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


@csrf_exempt
@require_POST
def payment_callback(request):
    """Уведомление шлюза о результате платежа. Защищено HMAC-подписью, не CSRF."""
    ctype = (request.content_type or '').lower()
    if 'application/json' in ctype:
        try:
            params = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return HttpResponseBadRequest('bad body')
    else:
        params = request.POST.dict()

    if not gateway.verify_signature(params):
        logger.warning('callback с неверной подписью: %s', params.get('orderId'))
        return HttpResponseBadRequest('bad signature')

    invoice_id = params.get('orderId') or params.get('order_id')
    payment = Payment.objects.filter(invoice_id=invoice_id).select_related('order').first()
    if payment is None:
        logger.warning('callback по неизвестному платежу: %s', invoice_id)
        return HttpResponse('OK')  # подтверждаем приём, повторять не нужно

    try:
        gateway.apply_callback(payment, params, source='callback')
    except Exception:  # noqa: BLE001 — callback обязан вернуть 200, иначе шлюз будет ретраить
        logger.exception('ошибка обработки callback по %s', invoice_id)

    return HttpResponse('OK')


def payment_failed(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    return render(request, 'payments/failed.html', {'order': order})


@require_POST
def payment_retry(request, order_id):
    """Повторная попытка оплаты по заказу — создаёт новый Payment и ведёт на форму."""
    order = get_object_or_404(Order, pk=order_id)
    if order.status not in (Order.Status.PENDING_PAYMENT, Order.Status.PAYMENT_FAILED, Order.Status.NEW):
        return redirect('main:order_success', order_id=order.pk)

    payment = gateway.create_payment(order)
    try:
        form_url = gateway.init_payment(
            payment,
            client_ip=_client_ip(request),
            success_url=request.build_absolute_uri(
                _reverse_success(order.pk)),
            fail_url=request.build_absolute_uri(
                _reverse_failed(order.pk)),
            callback_url=request.build_absolute_uri('/payments/callback/'),
        )
    except gateway.PaymentGatewayError:
        logger.exception('retry initPayment не удался для заказа %s', order.pk)
        return redirect('payments:failed', order_id=order.pk)

    order.status = Order.Status.PENDING_PAYMENT
    order.save(update_fields=['status', 'updated_at'])
    return redirect(form_url)


def _reverse_success(order_id):
    from django.urls import reverse
    return reverse('main:order_success', args=[order_id])


def _reverse_failed(order_id):
    from django.urls import reverse
    return reverse('payments:failed', args=[order_id])
