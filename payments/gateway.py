"""Интеграция с TipTop Pay (ex-CloudPayments Kazakhstan).

Документация: https://developers.tiptoppay.kz  (API-хост https://api.tiptoppay.kz)
Схема: счёт на оплату (/orders/create) -> редирект клиента на размещённую
форму оплаты (Model.Url) -> подписанное webhook-уведомление на /payments/callback/.

Авторизация всех запросов — HTTP Basic base64(PublicId:ApiSecret).
Подпись webhook — заголовок Content-HMAC: Base64(HMAC_SHA256(тело_запроса, ApiSecret)).
В ответ на уведомление шлюз ждёт JSON {"code": 0}.

  init_payment()      — создать счёт, получить ссылку на форму оплаты
  verify_signature()  — проверить Content-HMAC webhook'а по сырому телу
  apply_callback()    — идемпотентно применить результат к Payment и Order
  check_payment()     — синхронно спросить у шлюза статус (/payments/find)
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

Config = namedtuple('Config', 'public_id api_secret api_base currency enabled')

# Статусы платежа в ответах TipTop Pay (/payments/find, /payments/get).
_SUCCESS_STATUSES = {'Completed', 'Authorized'}
_FAIL_STATUSES = {'Declined', 'Cancelled'}


class PaymentGatewayError(Exception):
    """Ошибка обращения к шлюзу или отказ создания счёта."""


def get_config():
    """Реквизиты шлюза: сначала запись из админки, затем — значения из .env."""
    row = PaymentSettings.objects.filter(pk=1).first()
    if row and row.public_id and row.api_secret:
        return Config(
            public_id=row.public_id,
            api_secret=row.api_secret,
            api_base=(row.api_base or DEFAULT_API_BASE),
            currency=(row.currency or 'KZT'),
            enabled=bool(row.is_enabled),
        )
    return Config(
        public_id=settings.TIPTOP_PUBLIC_ID,
        api_secret=settings.TIPTOP_API_SECRET,
        api_base=(settings.TIPTOP_API_BASE or DEFAULT_API_BASE),
        currency=settings.PAYMENT_CURRENCY,
        enabled=bool(settings.TIPTOP_PUBLIC_ID and settings.TIPTOP_API_SECRET),
    )


def payments_enabled():
    c = get_config()
    return c.enabled and bool(c.public_id and c.api_secret)


def _auth_header():
    cfg = get_config()
    raw = f'{cfg.public_id}:{cfg.api_secret}'.encode()
    return 'Basic ' + base64.b64encode(raw).decode('ascii')


def _post(path, payload):
    """POST к API TipTop Pay. Возвращает (http_status, распарсенный JSON)."""
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


# --- подпись webhook-уведомления ----------------------------------------

def signature_for(raw_body):
    """Ожидаемое значение Content-HMAC для сырого тела запроса (bytes/str)."""
    if isinstance(raw_body, str):
        raw_body = raw_body.encode('utf-8')
    digest = hmac.new(
        get_config().api_secret.encode('utf-8'), raw_body, hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode('ascii')


def verify_signature(raw_body, provided_hmac):
    """Сверяет присланный заголовок Content-HMAC с посчитанным по телу запроса."""
    provided = (provided_hmac or '').strip()
    if not provided:
        return False
    return hmac.compare_digest(provided, signature_for(raw_body))


# --- создание счёта на оплату ------------------------------------------

def create_payment(order):
    """Создаёт запись Payment (ещё без обращения к шлюзу)."""
    return Payment.objects.create(
        order=order,
        amount=order.total_price,
        currency=get_config().currency,
        invoice_id=Payment.build_invoice_id(order),
    )


def init_payment(payment, *, success_url, fail_url, **_ignored):
    """Создаёт счёт через /orders/create, сохраняет form_url/external_id,
    возвращает ссылку на форму оплаты (Model.Url).

    Лишние именованные аргументы (client_ip, callback_url) допускаются и
    игнорируются: адрес webhook у TipTop Pay настраивается в личном кабинете,
    а не передаётся в запросе.
    """
    order = payment.order

    payload = {
        'Amount': float(payment.amount),
        'Currency': payment.currency,
        'Description': f'Заказ №{order.pk} — {settings.ADMIN_SITE_TITLE}',
        'InvoiceId': payment.invoice_id,
        'AccountId': (order.customer_email or f'order-{order.pk}'),
        'Email': order.customer_email or '',
        'Phone': order.customer_phone or '',
        'SuccessRedirectUrl': success_url,
        'FailRedirectUrl': fail_url,
        'JsonData': {'order_id': order.pk},
    }

    status_code, data = _post('/orders/create', payload)
    model = data.get('Model') or {}
    form_url = model.get('Url')
    if not data.get('Success') or not form_url:
        err = data.get('Message') or data.get('Model') or data
        logger.error('orders/create отказал: HTTP %s %s', status_code, err)
        raise PaymentGatewayError(str(err))

    payment.form_url = form_url
    payment.external_id = str(model.get('Id') or model.get('Number') or '') or None
    payment.save(update_fields=['form_url', 'external_id', 'updated_at'])
    return form_url


# --- обработка результата ---------------------------------------------

_TERMINAL = {Payment.Status.SUCCEEDED, Payment.Status.REFUNDED}


def _resolve_status(params, notification_type):
    """Куда переводить платёж. None — состояние не меняем (check / в процессе)."""
    ntype = (notification_type or '').lower()
    if ntype == 'refund':
        return Payment.Status.REFUNDED
    if ntype == 'fail':
        return Payment.Status.FAILED
    if ntype in ('pay', 'confirm'):
        return Payment.Status.SUCCEEDED
    if ntype == 'check':
        return None

    status = str(params.get('Status', ''))
    if status in _SUCCESS_STATUSES:
        return Payment.Status.SUCCEEDED
    if status in _FAIL_STATUSES:
        return Payment.Status.FAILED
    return None


def apply_callback(payment, params, *, source='callback', notification_type=None):
    """Идемпотентно применяет результат к Payment и его заказу.
    params — словарь полей webhook'а либо Model из ответа /payments/find."""
    payment.raw_callback_data = params

    new_status = _resolve_status(params, notification_type)
    if new_status is None:
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
    """Спрашивает у шлюза статус по invoice_id (/payments/find) и применяет его.
    Возвращает Model из ответа (dict) либо {}."""
    _status_code, data = _post('/payments/find', {'InvoiceId': payment.invoice_id})
    model = data.get('Model') or {}
    if model:
        apply_callback(payment, model, source=source)
    return model
