import hashlib
import hmac
import io
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

import json as _json

from django.contrib.auth.models import Group, User
from django.core.management import call_command

from accounts.models import SELLER_SECTION_GROUPS
from audit.models import AuditEvent
from catalog.models import Category, Product
from delivery.models import DeliveryZone
from orders.models import Order

from . import gateway
from .models import Payment, PaymentSettings

SETTINGS = dict(
    PAYMENTS_ENABLED=True,
    SMARTCORE_API_BASE='https://api.example.test',
    SMARTCORE_ACCOUNT='KZT-sandbox',
    SMARTCORE_MERCHANT_KEY='mkey',
    SMARTCORE_SECRET='s3cret',
    PAYMENT_CURRENCY='KZT',
)


def _img():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), '#BE9554').save(buf, format='JPEG')
    return SimpleUploadedFile('t.jpg', buf.getvalue(), content_type='image/jpeg')


def _sign(params, secret='s3cret'):
    keys = sorted(k for k in params if k != 'sign')
    base = '|'.join(str(params[k]) for k in keys)
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def _fake_response(payload, status=200):
    class R:
        status_code = status

        def json(self):
            return payload
    return R()


@override_settings(**SETTINGS)
class SignatureTests(TestCase):
    def test_verify_signature_roundtrip(self):
        params = {'orderId': 'bpf-1-abc', 'status': '2', 'amount': '1000',
                  'currency': 'KZT', 'type': 'Payment'}
        params['sign'] = _sign(params)
        self.assertTrue(gateway.verify_signature(params))

    def test_verify_signature_rejects_tampered(self):
        params = {'orderId': 'bpf-1-abc', 'status': '2', 'currency': 'KZT'}
        params['sign'] = _sign(params)
        params['status'] = '-1'  # подменили после подписи
        self.assertFalse(gateway.verify_signature(params))

    def test_verify_signature_rejects_missing(self):
        self.assertFalse(gateway.verify_signature({'orderId': 'x', 'status': '2'}))


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

    def _cb(self, **over):
        p = {'orderId': self.payment.invoice_id, 'status': '2', 'type': 'Payment',
             'amount': '12000', 'currency': 'KZT'}
        p.update(over)
        return p

    def test_success_marks_paid(self):
        changed = gateway.apply_callback(self.payment, self._cb())
        self.assertTrue(changed)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCEEDED)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_fail_marks_order_payment_failed(self):
        gateway.apply_callback(self.payment, self._cb(status='-1'))
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PAYMENT_FAILED)

    def test_refund_marks_refunded(self):
        gateway.apply_callback(self.payment, self._cb(status='2'))
        gateway.apply_callback(self.payment, self._cb(type='Refund', status='2'))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.REFUNDED)

    def test_repeated_success_callback_is_idempotent(self):
        self.assertTrue(gateway.apply_callback(self.payment, self._cb()))
        self.assertFalse(gateway.apply_callback(self.payment, self._cb()))
        self.assertEqual(Payment.objects.filter(status=Payment.Status.SUCCEEDED).count(), 1)

    def test_processing_status_does_not_change_state(self):
        self.assertFalse(gateway.apply_callback(self.payment, self._cb(status='1')))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)


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

    def test_valid_callback_marks_paid_and_returns_200(self):
        params = {'orderId': self.payment.invoice_id, 'status': '2', 'type': 'Payment',
                  'amount': '9000', 'currency': 'KZT'}
        params['sign'] = _sign(params)
        resp = self.client.post(reverse('payments:callback'), params)
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_bad_signature_is_rejected(self):
        params = {'orderId': self.payment.invoice_id, 'status': '2', 'sign': 'deadbeef'}
        resp = self.client.post(reverse('payments:callback'), params)
        self.assertEqual(resp.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_unknown_invoice_is_acknowledged(self):
        params = {'orderId': 'bpf-999-nope', 'status': '2', 'type': 'Payment'}
        params['sign'] = _sign(params)
        resp = self.client.post(reverse('payments:callback'), params)
        self.assertEqual(resp.status_code, 200)


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
            'delivery_zone': self.zone.pk, 'legal_consent': 'yes',
        }
        data.update(over)
        return self.client.post(reverse('main:checkout'), data)

    @patch('payments.gateway.requests.post')
    def test_checkout_creates_pending_order_and_redirects_to_form(self, mock_post):
        mock_post.return_value = _fake_response(
            {'status': 0, 'form_url': 'https://pay.example.test/xyz', 'order_id': 'GW-1'})
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
        # invoiceId уходит в шлюз как order_id
        sent = mock_post.call_args.kwargs['json']
        self.assertEqual(sent['order_id'], payment.invoice_id)
        self.assertEqual(sent['amount_major'], 20000.0)

    def test_email_is_required_when_payments_enabled(self):
        self._fill_cart()
        resp = self._checkout(customer_email='')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Укажите email')
        self.assertEqual(Order.objects.count(), 0)

    @patch('payments.gateway.requests.post')
    def test_gateway_failure_marks_order_and_shows_failed_page(self, mock_post):
        mock_post.return_value = _fake_response({'status': -1, 'err': 'declined'})
        self._fill_cart()
        resp = self._checkout()
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PAYMENT_FAILED)
        self.assertRedirects(resp, reverse('payments:failed', args=[order.pk]))

    @patch('payments.gateway.requests.post')
    def test_retry_creates_new_payment(self, mock_post):
        mock_post.return_value = _fake_response(
            {'status': 0, 'form_url': 'https://pay.example.test/retry', 'order_id': 'GW-2'})
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
        zone = DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )
        self.client.post(reverse('main:cart_add', args=[product.pk]), {'quantity': 1})
        resp = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Иван', 'customer_phone': '+77070000000',
            'delivery_address': 'ул. Абая, 10', 'delivery_zone': zone.pk, 'legal_consent': 'yes',
        })
        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.NEW)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertRedirects(resp, reverse('main:order_success', args=[order.pk]))


class PaymentSettingsModelTests(TestCase):
    def test_load_is_singleton(self):
        a = PaymentSettings.load()
        b = PaymentSettings.load()
        self.assertEqual(a.pk, 1)
        self.assertEqual(b.pk, 1)
        self.assertEqual(PaymentSettings.objects.count(), 1)

    def test_config_prefers_db_row_over_env(self):
        with override_settings(SMARTCORE_ACCOUNT='env-acc', SMARTCORE_MERCHANT_KEY='env-key',
                               SMARTCORE_SECRET='env-sec'):
            row = PaymentSettings.load()
            row.is_enabled = True
            row.account = 'db-acc'
            row.merchant_key = 'db-key'
            row.secret = 'db-sec'
            row.api_base = 'https://db.example.test'
            row.save()

            cfg = gateway.get_config()
            self.assertEqual(cfg.account, 'db-acc')
            self.assertEqual(cfg.secret, 'db-sec')
            self.assertEqual(cfg.api_base, 'https://db.example.test')
            self.assertTrue(gateway.payments_enabled())

    def test_disabled_flag_turns_payments_off_even_with_creds(self):
        row = PaymentSettings.load()
        row.account, row.merchant_key, row.secret = 'a', 'k', 's'
        row.is_enabled = False
        row.save()
        self.assertFalse(gateway.payments_enabled())

    def test_env_fallback_when_no_db_row(self):
        with override_settings(SMARTCORE_ACCOUNT='', SMARTCORE_MERCHANT_KEY='', SMARTCORE_SECRET=''):
            self.assertFalse(gateway.payments_enabled())
        with override_settings(SMARTCORE_ACCOUNT='a', SMARTCORE_MERCHANT_KEY='k', SMARTCORE_SECRET='s'):
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

    def test_blank_secret_keeps_existing_value_and_is_not_logged(self):
        row = PaymentSettings.load()
        row.secret = 'existing-secret'
        row.save()

        self.client.force_login(self.owner)
        resp = self.client.post(self.url, {
            'is_enabled': 'on', 'account': 'ACC', 'merchant_key': 'KEY',
            'secret': '',  # не трогаем
            'api_base': 'https://api-gateway.smartcore.pro', 'currency': 'KZT',
        })
        self.assertEqual(resp.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.secret, 'existing-secret')
        self.assertEqual(row.account, 'ACC')

        events = AuditEvent.objects.filter(action=AuditEvent.Action.UPDATE)
        blob = _json.dumps([e.changes for e in events])
        self.assertNotIn('existing-secret', blob)

    def test_new_secret_is_redacted_in_audit_log(self):
        PaymentSettings.load()
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            'is_enabled': 'on', 'account': 'ACC', 'merchant_key': 'KEY',
            'secret': 'brand-new-secret-value',
            'api_base': 'https://api-gateway.smartcore.pro', 'currency': 'KZT',
        })
        PaymentSettings.load().refresh_from_db()
        self.assertEqual(PaymentSettings.objects.get(pk=1).secret, 'brand-new-secret-value')

        event = AuditEvent.objects.filter(action=AuditEvent.Action.UPDATE).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.changes.get('secret'), ['***', '***'])
        self.assertNotIn('brand-new-secret-value', _json.dumps(event.changes))


@override_settings(SMARTCORE_ACCOUNT='', SMARTCORE_MERCHANT_KEY='', SMARTCORE_SECRET='')
class CheckoutUsesDbConfigTests(TestCase):
    def setUp(self):
        row = PaymentSettings.load()
        row.is_enabled = True
        row.account, row.merchant_key, row.secret = 'db-account', 'db-key', 'db-secret'
        row.api_base = 'https://db-gateway.test'
        row.save()

        self.category = Category.objects.create(name='Кат')
        self.product = Product.objects.create(
            name='Букет', category=self.category, price=Decimal('12000'), in_stock=True, image=_img(),
        )
        self.zone = DeliveryZone.objects.create(
            name='Р', radius_from_km=0, radius_to_km=5, price=Decimal('0'),
        )

    @patch('payments.gateway.requests.post')
    def test_checkout_calls_gateway_with_db_credentials(self, mock_post):
        mock_post.return_value = _fake_response(
            {'status': 0, 'form_url': 'https://db-gateway.test/form/1', 'order_id': 'X1'})
        self.client.post(reverse('main:cart_add', args=[self.product.pk]), {'quantity': 1})
        resp = self.client.post(reverse('main:checkout'), {
            'customer_name': 'Иван Петров', 'customer_phone': '+77070000000',
            'customer_email': 'i@e.com', 'delivery_address': 'ул. Абая, 10',
            'delivery_zone': self.zone.pk, 'legal_consent': 'yes',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://db-gateway.test/form/1')

        called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get('url')
        self.assertTrue(called_url.startswith('https://db-gateway.test/'))
        sent = mock_post.call_args.kwargs['json']
        self.assertEqual(sent['account'], 'db-account')
