import base64
import hashlib
import hmac
import io
import json as _json
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import SELLER_SECTION_GROUPS
from audit.models import AuditEvent
from catalog.models import Category, Product
from delivery.models import DeliveryZone
from orders.models import Order

from . import gateway
from .models import Payment, PaymentSettings

SECRET = 's3cret'

SETTINGS = dict(
    PAYMENTS_ENABLED=True,
    TIPTOP_API_BASE='https://api.example.test',
    TIPTOP_PUBLIC_ID='pk_test',
    TIPTOP_API_SECRET=SECRET,
    PAYMENT_CURRENCY='KZT',
)


def _img():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), '#BE9554').save(buf, format='JPEG')
    return SimpleUploadedFile('t.jpg', buf.getvalue(), content_type='image/jpeg')


def _content_hmac(raw_body, secret=SECRET):
    """Заголовок Content-HMAC так, как его считает TipTop Pay."""
    if isinstance(raw_body, str):
        raw_body = raw_body.encode('utf-8')
    digest = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode('ascii')


def _orders_create_ok(url='https://pay.example.test/xyz', ext_id='GW-1', number=1):
    return _fake_response({'Success': True, 'Model': {
        'Id': ext_id, 'Number': number, 'Url': url,
    }})


def _fake_response(payload, status=200):
    class R:
        status_code = status

        def json(self):
            return payload
    return R()


# ----------------------------------------------------------------------
#  Подпись webhook (Content-HMAC по сырому телу)
# ----------------------------------------------------------------------
@override_settings(**SETTINGS)
class SignatureTests(TestCase):
    def test_verify_signature_roundtrip(self):
        body = b'TransactionId=42&InvoiceId=bpf-1-abc&Status=Completed'
        self.assertTrue(gateway.verify_signature(body, _content_hmac(body)))

    def test_verify_signature_rejects_tampered_body(self):
        body = b'TransactionId=42&InvoiceId=bpf-1-abc&Status=Completed'
        sig = _content_hmac(body)
        self.assertFalse(gateway.verify_signature(body + b'&x=1', sig))

    def test_verify_signature_rejects_wrong_secret(self):
        body = b'Status=Completed'
        self.assertFalse(gateway.verify_signature(body, _content_hmac(body, 'other')))

    def test_verify_signature_rejects_missing(self):
        self.assertFalse(gateway.verify_signature(b'Status=Completed', ''))
        self.assertFalse(gateway.verify_signature(b'Status=Completed', None))


# ----------------------------------------------------------------------
#  apply_callback: перевод Payment/Order по результату
# ----------------------------------------------------------------------
@override_settings(**SETTINGS)
class ApplyCallbackTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            status=Order.Status.PENDING_PAYMENT,
            customer_name='Аня', customer_phone='+77000000000',
            customer_email='a@e.com', delivery_address='ул. Тест, 1',
            total_price=Decimal('12000'),
        )
        self.payment = Payment.objects.create(
            order=self.order, amount=self.order.total_price, currency='KZT',
            invoice_id=Payment.build_invoice_id(self.order),
        )

    def _params(self, **over):
        p = {'InvoiceId': self.payment.invoice_id, 'Status': 'Completed',
             'Amount': '12000', 'Currency': 'KZT', 'TransactionId': '777'}
        p.update(over)
        return p

    def test_completed_status_marks_paid(self):
        changed = gateway.apply_callback(self.payment, self._params())
        self.assertTrue(changed)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_pay_notification_type_marks_paid_even_without_status(self):
        gateway.apply_callback(self.payment, self._params(Status=''),
                               notification_type='pay')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)

    def test_declined_status_marks_order_payment_failed(self):
        gateway.apply_callback(self.payment, self._params(Status='Declined'))
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PAYMENT_FAILED)

    def test_fail_notification_type_marks_failed(self):
        gateway.apply_callback(self.payment, self._params(Status=''),
                               notification_type='fail')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)

    def test_refund_notification_marks_refunded(self):
        gateway.apply_callback(self.payment, self._params())
        gateway.apply_callback(self.payment, self._params(), notification_type='refund')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)

    def test_repeated_success_is_idempotent(self):
        self.assertTrue(gateway.apply_callback(self.payment, self._params()))
        self.assertFalse(gateway.apply_callback(self.payment, self._params()))
        self.assertEqual(Payment.objects.filter(status=Payment.Status.SUCCEEDED).count(), 1)

    def test_check_notification_does_not_change_state(self):
        self.assertFalse(gateway.apply_callback(
            self.payment, self._params(Status=''), notification_type='check'))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_unknown_status_does_not_change_state(self):
        self.assertFalse(gateway.apply_callback(
            self.payment, self._params(Status='AwaitingAuthentication')))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)


# ----------------------------------------------------------------------
#  Вью webhook'а
# ----------------------------------------------------------------------
@override_settings(**SETTINGS)
class CallbackViewTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            status=Order.Status.PENDING_PAYMENT, customer_name='Аня',
            customer_phone='+77000000000', customer_email='a@e.com',
            delivery_address='ул. Тест, 1', total_price=Decimal('9000'),
        )
        self.payment = Payment.objects.create(
            order=self.order, amount=Decimal('9000'), currency='KZT',
            invoice_id=Payment.build_invoice_id(self.order),
        )

    def _post(self, fields, *, sign=True, query=''):
        body = urlencode(fields)
        headers = {}
        if sign:
            headers['HTTP_CONTENT_HMAC'] = _content_hmac(body)
        return self.client.post(
            reverse('payments:callback') + query, data=body,
            content_type='application/x-www-form-urlencoded', **headers,
        )

    def test_valid_pay_webhook_marks_paid_and_returns_code_0(self):
        resp = self._post({
            'InvoiceId': self.payment.invoice_id, 'Status': 'Completed',
            'Amount': '9000', 'Currency': 'KZT', 'TransactionId': '1',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'code': 0})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_type_query_param_overrides_status(self):
        resp = self._post(
            {'InvoiceId': self.payment.invoice_id, 'TransactionId': '1'},
            query='?type=fail',
        )
        self.assertEqual(resp.json(), {'code': 0})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAYMENT_FAILED)

    def test_bad_signature_is_rejected_and_state_unchanged(self):
        body = urlencode({'InvoiceId': self.payment.invoice_id, 'Status': 'Completed'})
        resp = self.client.post(
            reverse('payments:callback'), data=body,
            content_type='application/x-www-form-urlencoded',
            HTTP_CONTENT_HMAC='not-a-valid-hmac',
        )
        self.assertEqual(resp.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_missing_signature_header_is_rejected(self):
        resp = self._post({'InvoiceId': self.payment.invoice_id, 'Status': 'Completed'},
                          sign=False)
        self.assertEqual(resp.status_code, 400)

    def test_unknown_invoice_is_acknowledged(self):
        resp = self._post({'InvoiceId': 'bpf-999-nope', 'Status': 'Completed'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'code': 0})


# ----------------------------------------------------------------------
#  Чекаут с включённой онлайн-оплатой
# ----------------------------------------------------------------------
@override_settings(**SETTINGS)
class CheckoutWithPaymentsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Кат')
        self.product = Product.objects.create(
            name='Букет', category=self.category, price=Decimal('18500'),
            in_stock=True, image=_img(),
        )
        self.zone = DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )

    def _fill_cart(self):
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})

    def _checkout(self, **over):
        data = {
            'customer_name': 'Иван Петров', 'customer_phone': '+77070000000',
            'customer_email': 'ivan@example.com', 'delivery_address': 'ул. Абая, 10',
            'legal_consent': 'yes',
        }
        data.update(over)
        return self.client.post(reverse('main:checkout'), data)

    @patch('main.views.quote_delivery')
    @patch('payments.gateway.requests.post')
    def test_checkout_creates_pending_order_and_redirects_to_form(self, mock_post, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        mock_post.return_value = _orders_create_ok(
            url='https://pay.example.test/xyz', ext_id='GW-1')
        self._fill_cart()
        resp = self._checkout()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://pay.example.test/xyz')

        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        payment = order.payments.get()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.external_id, 'GW-1')
        self.assertEqual(payment.form_url, 'https://pay.example.test/xyz')

        called_url = mock_post.call_args.args[0] if mock_post.call_args.args \
            else mock_post.call_args.kwargs.get('url')
        self.assertEqual(called_url, 'https://api.example.test/orders/create')
        sent = mock_post.call_args.kwargs['json']
        self.assertEqual(sent['InvoiceId'], payment.invoice_id)
        self.assertEqual(sent['Amount'], 20000.0)
        self.assertEqual(sent['Currency'], 'KZT')

    @patch('payments.gateway.requests.post')
    def test_auth_header_is_basic_public_id_and_secret(self, mock_post):
        mock_post.return_value = _orders_create_ok()
        order = Order.objects.create(
            status=Order.Status.PAYMENT_FAILED, customer_name='Иван',
            customer_phone='+77070000000', customer_email='i@e.com',
            delivery_address='ул. Абая, 10', total_price=Decimal('20000'),
        )
        self.client.post(reverse('payments:retry', args=[order.pk]))
        expected = 'Basic ' + base64.b64encode(b'pk_test:s3cret').decode()
        self.assertEqual(mock_post.call_args.kwargs['headers']['Authorization'], expected)

    def test_email_is_required_when_payments_enabled(self):
        self._fill_cart()
        resp = self._checkout(customer_email='')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Укажите email')
        self.assertEqual(Order.objects.count(), 0)

    @patch('main.views.quote_delivery')
    @patch('payments.gateway.requests.post')
    def test_unconfirmed_delivery_never_starts_payment(self, mock_post, quote):
        quote.return_value = (None, Decimal('0'), None, 'out_of_zone')
        self._fill_cart()
        resp = self._checkout()
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertEqual(order.payments.count(), 0)
        mock_post.assert_not_called()
        self.assertRedirects(resp, reverse('main:order_success', args=[order.pk]))

    @patch('main.views.quote_delivery')
    @patch('payments.gateway.requests.post')
    def test_gateway_unreachable_falls_back_to_manager_not_500(self, mock_post, quote):
        # Шлюз недоступен: заказ не теряется и не помечается «ошибка оплаты» —
        # остаётся обычной заявкой, клиент видит страницу «заказ принят».
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        mock_post.side_effect = gateway.requests.ConnectionError('no route to host')
        self._fill_cart()
        resp = self._checkout()
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertIn('ОНЛАЙН-ОПЛАТА НЕ ЗАПУСТИЛАСЬ', order.comment)
        self.assertRedirects(resp, reverse('main:order_success', args=[order.pk]))

    @patch('main.views.quote_delivery')
    @patch('payments.gateway.requests.post')
    def test_gateway_rejects_order_also_falls_back_to_manager(self, mock_post, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        mock_post.return_value = _fake_response({'Success': False, 'Message': 'declined'})
        self._fill_cart()
        resp = self._checkout()
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertRedirects(resp, reverse('main:order_success', args=[order.pk]))

    @patch('payments.gateway.requests.post')
    def test_retry_creates_new_payment(self, mock_post):
        mock_post.return_value = _orders_create_ok(
            url='https://pay.example.test/retry', ext_id='GW-2')
        order = Order.objects.create(
            status=Order.Status.PAYMENT_FAILED, customer_name='Иван Петров',
            customer_phone='+77070000000', customer_email='ivan@example.com',
            delivery_address='ул. Абая, 10', total_price=Decimal('20000'),
        )
        resp = self.client.post(reverse('payments:retry', args=[order.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://pay.example.test/retry')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(order.payments.count(), 1)


@override_settings(PAYMENTS_ENABLED=False)
class CheckoutWithoutPaymentsTests(TestCase):
    def test_checkout_keeps_legacy_flow_and_creates_no_payment(self):
        category = Category.objects.create(name='Кат')
        product = Product.objects.create(
            name='Букет', category=category, price=Decimal('5000'), in_stock=True, image=_img(),
        )
        DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )
        self.client.post(reverse('main:cart_add', args=[product.pk]), {'quantity': 1})
        resp = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Иван', 'customer_phone': '+77070000000',
            'delivery_address': 'ул. Абая, 10', 'legal_consent': 'yes',
        })
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertRedirects(resp, reverse('main:order_success', args=[order.pk]))


# ----------------------------------------------------------------------
#  Настройки онлайн-оплаты (синглтон + приоритет БД над .env)
# ----------------------------------------------------------------------
class PaymentSettingsModelTests(TestCase):
    def test_load_is_singleton(self):
        a = PaymentSettings.load()
        b = PaymentSettings.load()
        self.assertEqual(a.pk, 1)
        self.assertEqual(b.pk, 1)
        self.assertEqual(PaymentSettings.objects.count(), 1)

    def test_config_prefers_db_row_over_env(self):
        with override_settings(TIPTOP_PUBLIC_ID='pk_env', TIPTOP_API_SECRET='env-sec'):
            row = PaymentSettings.load()
            row.is_enabled = True
            row.public_id = 'pk_db'
            row.api_secret = 'db-sec'
            row.api_base = 'https://db.example.test'
            row.save()

            cfg = gateway.get_config()
            self.assertEqual(cfg.public_id, 'pk_db')
            self.assertEqual(cfg.api_secret, 'db-sec')
            self.assertEqual(cfg.api_base, 'https://db.example.test')
            self.assertTrue(gateway.payments_enabled())

    def test_disabled_flag_turns_payments_off_even_with_creds(self):
        row = PaymentSettings.load()
        row.public_id, row.api_secret = 'pk_x', 'sec'
        row.is_enabled = False
        row.save()
        self.assertFalse(gateway.payments_enabled())

    def test_env_fallback_when_no_db_row(self):
        with override_settings(TIPTOP_PUBLIC_ID='', TIPTOP_API_SECRET=''):
            self.assertFalse(gateway.payments_enabled())
        with override_settings(TIPTOP_PUBLIC_ID='pk_e', TIPTOP_API_SECRET='s'):
            self.assertTrue(gateway.payments_enabled())


class PaymentSettingsAdminTests(TestCase):
    def setUp(self):
        call_command('setup_roles')
        self.owner = User.objects.create_superuser('owner', 'o@e.com', 'pass12345')
        self.seller = User.objects.create_user('seller', password='pass12345', is_staff=True)
        self.seller.groups.add(Group.objects.get(name=SELLER_SECTION_GROUPS['catalog']))
        self.url = reverse('admin:payments_paymentsettings_change', args=[1])

    def test_only_superuser_can_open_settings(self):
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.owner)
        PaymentSettings.load()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_changelist_redirects_to_the_single_object(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse('admin:payments_paymentsettings_changelist'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/1/change/', resp['Location'])

    def test_blank_api_secret_keeps_existing_value_and_is_not_logged(self):
        row = PaymentSettings.load()
        row.api_secret = 'existing-secret'
        row.save()

        self.client.force_login(self.owner)
        resp = self.client.post(self.url, {
            'is_enabled': 'on', 'public_id': 'pk_new',
            'api_secret': '',  # не трогаем
            'api_base': 'https://api.tiptoppay.kz', 'currency': 'KZT',
        })
        self.assertEqual(resp.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.api_secret, 'existing-secret')
        self.assertEqual(row.public_id, 'pk_new')

        events = AuditEvent.objects.filter(action=AuditEvent.Action.UPDATE)
        blob = _json.dumps([e.changes for e in events])
        self.assertNotIn('existing-secret', blob)

    def test_new_api_secret_is_redacted_in_audit_log(self):
        PaymentSettings.load()
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            'is_enabled': 'on', 'public_id': 'pk_new',
            'api_secret': 'brand-new-secret-value',
            'api_base': 'https://api.tiptoppay.kz', 'currency': 'KZT',
        })
        self.assertEqual(PaymentSettings.objects.get(pk=1).api_secret, 'brand-new-secret-value')

        event = AuditEvent.objects.filter(action=AuditEvent.Action.UPDATE).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.changes.get('api_secret'), ['***', '***'])
        self.assertNotIn('brand-new-secret-value', _json.dumps(event.changes))


@override_settings(TIPTOP_PUBLIC_ID='', TIPTOP_API_SECRET='')
class CheckoutUsesDbConfigTests(TestCase):
    def setUp(self):
        row = PaymentSettings.load()
        row.is_enabled = True
        row.public_id, row.api_secret = 'pk_db', 'db-secret'
        row.api_base = 'https://db-gateway.test'
        row.save()

        self.category = Category.objects.create(name='Кат')
        self.product = Product.objects.create(
            name='Букет', category=self.category, price=Decimal('12000'), in_stock=True, image=_img(),
        )
        self.zone = DeliveryZone.objects.create(
            name='Р', radius_from_km=0, radius_to_km=5, price=Decimal('0'),
        )

    @patch('main.views.quote_delivery')
    @patch('payments.gateway.requests.post')
    def test_checkout_calls_gateway_with_db_credentials(self, mock_post, quote):
        quote.return_value = (self.zone, self.zone.price, 1.0, 'ok')
        mock_post.return_value = _orders_create_ok(url='https://db-gateway.test/form/1')
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        resp = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Иван Петров', 'customer_phone': '+77070000000',
            'customer_email': 'i@e.com', 'delivery_address': 'ул. Абая, 10',
            'legal_consent': 'yes',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://db-gateway.test/form/1')

        called_url = mock_post.call_args.args[0] if mock_post.call_args.args \
            else mock_post.call_args.kwargs.get('url')
        self.assertTrue(called_url.startswith('https://db-gateway.test/'))
        auth = mock_post.call_args.kwargs['headers']['Authorization']
        self.assertEqual(auth, 'Basic ' + base64.b64encode(b'pk_db:db-secret').decode())
