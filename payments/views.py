import logging

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from orders.models import Order

from . import gateway
from .models import Payment

logger = logging.getLogger('payments')

# Ответ, которого TipTop Pay ждёт для «уведомление принято».
_ACK = {'code': 0}


@csrf_exempt
@require_POST
def payment_callback(request):
    """Webhook TipTop Pay (pay / fail / refund / check). Защищён HMAC-подписью,
    не CSRF. Тип уведомления берём из ?type= (его задаём в адресе в ЛК шлюза),
    иначе выводим из поля Status. В ответ шлюз ждёт JSON {"code": 0}."""
    raw_body = request.body
    provided = request.headers.get('Content-HMAC') or request.headers.get('X-Content-HMAC')
    if not gateway.verify_signature(raw_body, provided):
        logger.warning('webhook с неверной подписью Content-HMAC')
        return HttpResponseBadRequest('bad signature')

    params = request.POST.dict()
    notification_type = request.GET.get('type', '')
    invoice_id = params.get('InvoiceId')
    payment = Payment.objects.filter(invoice_id=invoice_id).select_related('order').first()
    if payment is None:
        logger.warning('webhook по неизвестному платежу: %s', invoice_id)
        return JsonResponse(_ACK)  # приняли, повторять не нужно

    try:
        gateway.apply_callback(
            payment, params, source='callback', notification_type=notification_type,
        )
    except Exception:  # noqa: BLE001 — webhook обязан вернуть 200, иначе шлюз будет ретраить
        logger.exception('ошибка обработки webhook по %s', invoice_id)

    return JsonResponse(_ACK)


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
            success_url=request.build_absolute_uri(_reverse_success(order.pk)),
            fail_url=request.build_absolute_uri(_reverse_failed(order.pk)),
        )
    except gateway.PaymentGatewayError:
        logger.exception('retry orders/create не удался для заказа %s', order.pk)
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
