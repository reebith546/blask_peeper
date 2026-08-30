"""Интеграция с платёжным шлюзом TipTop Pay / SmartCore.

Документация: https://docs.smartcore.pro
Схема: hosted payment form (редирект) + подписанный callback.

  init_payment()      — создать платёж, получить ссылку на форму оплаты
  verify_signature()  — проверить подпись callback'а
  apply_callback()    — идемпотентно применить результат к Payment и Order
  check_payment()     — синхронно спросить у шлюза статус (сверка)
"""
import base64
import hashlib
import hmac
import logging
from collections import namedtuple

import requests
from django.conf import settings
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from orders.models import Order

from .models import DEFAULT_API_BASE, Payment, PaymentSettings

logger = logging.getLogger('payments')

_TIMEOUT = 30

Config = namedtuple('Config', 'account merchant_key secret api_base currency enabled')


class PaymentGatewayError(Exception):
    """Ошибка обращения к шлюзу или отказ инициализации платежа."""


def get_config():
    """Реквизиты шлюза: сначала запись из админки, затем — значения из .env."""
    row = PaymentSettings.objects.filter(pk=1).first()
    if row and row.account and row.merchant_key and row.secret:
        return Config(
            account=row.account,
            merchant_key=row.merchant_key,
            secret=row.secret,
            api_base=(row.api_base or DEFAULT_API_BASE),
            currency=(row.currency or 'KZT'),
            enabled=bool(row.is_enabled),
        )
    return Config(
        account=settings.SMARTCORE_ACCOUNT,
        merchant_key=settings.SMARTCORE_MERCHANT_KEY,
        secret=settings.SMARTCORE_SECRET,
        api_base=(settings.SMARTCORE_API_BASE or DEFAULT_API_BASE),
        currency=settings.PAYMENT_CURRENCY,
        enabled=bool(settings.SMARTCORE_ACCOUNT and settings.SMARTCORE_MERCHANT_KEY
                     and settings.SMARTCORE_SECRET),
    )


def payments_enabled():
    c = get_config()
    return c.enabled and bool(c.account and c.merchant_key and c.secret)


def _auth_header():
    cfg = get_config()
    raw = f'{cfg.merchant_key}:{cfg.secret}'.encode()
    return 'Basic ' + base64.b64encode(raw).decode('ascii')


def _post(path, payload):
    url = get_config().api_base.rstrip('/') + path
    try:
        resp = requests.post(
            url, json=payload, timeout=_TIMEOUT,
            headers={'Authorization': _auth_header(), 'Accept': 'application/json'},
        )
    except requests.RequestException as exc:
        raise PaymentGatewayError(f'сеть: {exc}') from exc
    try:
        data = resp.json()
    except ValueError:
        raise PaymentGatewayError(f'HTTP {resp.status_code}: ответ не JSON')
    return resp.status_code, data


# --- подпись callback'а ----------------------------------------------------

def signature_for(params):
    """Подпись SmartCore: значения всех параметров (кроме `sign`),
    отсортированные ПО КЛЮЧУ, склеенные через '|', HMAC-SHA256, hex."""
    keys = sorted(k for k in params if k != 'sign')
    base_str = '|'.join(str(params[k]) for k in keys)
    return hmac.new(
        get_config().secret.encode(), base_str.encode(), hashlib.sha256,
    ).hexdigest()


def verify_signature(params):
    got = str(params.get('sign', ''))
    if not got:
        return False
    return hmac.compare_digest(got, signature_for(params))


# --- инициализация платежа ----------------------------------------------

def create_payment(order):
    """Создаёт запись Payment (ещё без обращения к шлюзу)."""
    return Payment.objects.create(
        order=order,
        amount=order.total_price,
        currency=get_config().currency,
        invoice_id=Payment.build_invoice_id(order),
    )


def init_payment(payment, *, client_ip, success_url, fail_url, callback_url):
    """Вызывает initPayment, сохраняет form_url/external_id, возвращает form_url."""
    order = payment.order
    first_name, _, last_name = (order.customer_name or '').strip().partition(' ')

    payload = {
        'account': get_config().account,
        'currency': payment.currency,
        'order_id': payment.invoice_id,
        'amount_major': float(payment.amount),
        'purpose': f'Заказ №{order.pk} — {settings.ADMIN_SITE_TITLE}',
        'customer_first_name': first_name or 'Client',
        'customer_last_name': last_name or '-',
        'customer_email': order.customer_email,
        'customer_phone': order.customer_phone,
        'customer_address': order.delivery_address,
        'customer_city': settings.PAYMENT_CUSTOMER_CITY,
        'customer_zip_code': settings.PAYMENT_CUSTOMER_ZIP,
        'customer_country': settings.PAYMENT_CUSTOMER_COUNTRY,
        'customer_ip_address': client_ip or '127.0.0.1',
        'success_url': success_url,
        'fail_url': fail_url,
        'callback_url': callback_url,
    }

    status_code, data = _post('/initPayment', payload)
    form_url = data.get('form_url')
    if not form_url:
        err = data.get('err') or data.get('errorMessage') or data.get('error') or data
        logger.error('initPayment отказал: HTTP %s %s', status_code, err)
        raise PaymentGatewayError(str(err))

    payment.form_url = form_url
    payment.external_id = str(data.get('order_id') or '') or None
    payment.save(update_fields=['form_url', 'external_id', 'updated_at'])
    return form_url


# --- обработка результата ---------------------------------------------

_TERMINAL = {Payment.Status.SUCCEEDED, Payment.Status.REFUNDED}


def apply_callback(payment, params, *, source='callback'):
    """Идемпотентно применяет результат к Payment и его заказу.
    params — словарь параметров callback'а или ответа /check."""
    payment.raw_callback_data = params

    kind = str(params.get('type', 'Payment'))
    status = str(params.get('status', ''))

    if kind == 'Refund':
        new_status = Payment.Status.REFUNDED
    elif status == '2':
        new_status = Payment.Status.SUCCEEDED
    elif status in ('-1', '3'):
        new_status = Payment.Status.FAILED
    else:
        # 0/1 — ещё в процессе, ничего не меняем.
        payment.save(update_fields=['raw_callback_data', 'updated_at'])
        return False

    if payment.status in _TERMINAL and payment.status == new_status:
        payment.save(update_fields=['raw_callback_data', 'updated_at'])
        return False  # уже применяли — идемпотентность

    payment.status = new_status
    if new_status == Payment.Status.SUCCEEDED and payment.paid_at is None:
        payment.paid_at = timezone.now()
    payment.save()

    order = payment.order
    if new_status == Payment.Status.SUCCEEDED:
        order.status = Order.Status.PAID
    elif new_status == Payment.Status.FAILED and order.status in (
        Order.Status.PENDING_PAYMENT, Order.Status.NEW,
    ):
        order.status = Order.Status.PAYMENT_FAILED
    elif new_status == Payment.Status.REFUNDED:
        order.status = Order.Status.PAYMENT_FAILED
    order.save(update_fields=['status', 'updated_at'])

    record(
        action=AuditEvent.Action.SYSTEM,
        actor_role=AuditEvent.Role.SYSTEM,
        target=payment,
        context={'event': f'payment_{new_status}', 'source': source,
                 'order_id': order.pk, 'invoice_id': payment.invoice_id},
    )
    return True


def check_payment(payment, *, source='check'):
    """Спрашивает у шлюза статус по invoice_id и применяет его. Возвращает dict ответа."""
    _status_code, data = _post('/check', {'order_id': payment.invoice_id})
    # /check и callback используют одинаковые поля status/type — переиспользуем apply_callback.
    apply_callback(payment, {'status': data.get('status'), 'type': data.get('type', 'Payment'),
                             **{k: v for k, v in data.items() if isinstance(v, (str, int, float))}},
                   source=source)
    return data
