from decimal import Decimal
from unittest.mock import patch

import requests
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product
from delivery.models import DeliveryZone
from orders.models import Order, OrderItem

from . import services
from .models import TelegramRecipient, TelegramSettings


def _fake_response(payload, status=200):
    class R:
        status_code = status

        def json(self):
            return payload
    return R()


class TelegramSettingsModelTests(TestCase):
    def test_load_creates_singleton(self):
        a = TelegramSettings.load()
        b = TelegramSettings.load()
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(TelegramSettings.objects.count(), 1)

    def test_is_ready_requires_enabled_token_and_at_least_one_recipient(self):
        row = TelegramSettings.load()
        self.assertFalse(row.is_ready)
        row.is_enabled = True
        row.bot_token = 't'
        self.assertFalse(row.is_ready)  # ещё нет ни одного получателя
        TelegramRecipient.objects.create(chat_id='1')
        self.assertTrue(row.is_ready)
        row.is_enabled = False
        self.assertFalse(row.is_ready)

    def test_new_recipient_auto_attaches_to_the_singleton(self):
        TelegramSettings.load()
        recipient = TelegramRecipient.objects.create(chat_id='777')
        self.assertEqual(recipient.settings_id, 1)


class NotificationsEnabledTests(TestCase):
    def test_disabled_without_settings_row(self):
        self.assertFalse(services.notifications_enabled())

    def test_disabled_without_any_recipient(self):
        TelegramSettings.objects.create(is_enabled=True, bot_token='t')
        self.assertFalse(services.notifications_enabled())

    def test_enabled_when_configured(self):
        TelegramSettings.objects.create(is_enabled=True, bot_token='t')
        TelegramRecipient.objects.create(chat_id='c')
        self.assertTrue(services.notifications_enabled())

    def test_disabled_when_flag_off(self):
        TelegramSettings.objects.create(is_enabled=False, bot_token='t')
        TelegramRecipient.objects.create(chat_id='c')
        self.assertFalse(services.notifications_enabled())


class FormatAndSendTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Кат')
        self.product = Product.objects.create(
            name='Wild Kilimanjaro', category=self.category, price=Decimal('87000'), in_stock=True,
        )
        TelegramSettings.objects.create(is_enabled=True, bot_token='t-token')
        self.chat_a = TelegramRecipient.objects.create(chat_id='42', label='Директор')
        self.chat_b = TelegramRecipient.objects.create(chat_id='43', label='Менеджеры')

    def test_format_includes_bouquet_order_number_status_and_prices(self):
        order = Order.objects.create(
            customer_name='Иван', customer_phone='+77070000000',
            delivery_address='ул. Абая, 10', total_price=Decimal('88500'),
            status=Order.Status.PENDING_PAYMENT, delivery_price=Decimal('1500'),
        )
        zone = DeliveryZone.objects.create(
            name='Район', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )
        order.delivery_zone = zone
        order.save(update_fields=['delivery_zone'])
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=self.product.price)

        text = services._format_new_order(order)
        self.assertIn(f'Новый заказ №{order.pk}', text)
        self.assertIn('Wild Kilimanjaro ×1', text)
        self.assertIn('87 000', text)  # цена букета
        self.assertIn('1 500', text)  # цена доставки
        self.assertIn('88 500', text)  # итого
        self.assertIn('Ожидает оплаты', text)

    def test_format_pickup_order_shows_self_pickup(self):
        order = Order.objects.create(
            customer_name='Иван', customer_phone='+77070000000',
            total_price=self.product.price, delivery_method=Order.DeliveryMethod.PICKUP,
        )
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=self.product.price)
        text = services._format_new_order(order)
        self.assertIn('Самовывоз', text)

    @patch('notify.services.requests.post')
    def test_send_message_reaches_every_recipient(self, mock_post):
        mock_post.return_value = _fake_response({'ok': True})
        self.assertTrue(services.send_message('привет'))
        self.assertEqual(mock_post.call_count, 2)
        sent_chat_ids = {c.kwargs['json']['chat_id'] for c in mock_post.call_args_list}
        self.assertEqual(sent_chat_ids, {'42', '43'})
        called_url = mock_post.call_args.args[0]
        self.assertEqual(called_url, 'https://api.telegram.org/bott-token/sendMessage')

    @patch('notify.services.requests.post')
    def test_send_message_true_if_at_least_one_recipient_got_it(self, mock_post):
        def fake_post(url, timeout, json):
            ok = json['chat_id'] == '42'
            return _fake_response({'ok': ok, 'description': 'blocked' if not ok else None})
        mock_post.side_effect = fake_post
        self.assertTrue(services.send_message('привет'))

    @patch('notify.services.requests.post')
    def test_send_message_false_when_nobody_receives_it(self, mock_post):
        mock_post.return_value = _fake_response({'ok': False, 'description': 'bad token'})
        self.assertFalse(services.send_message('привет'))

    @patch('notify.services.requests.post', side_effect=requests.ConnectionError('boom'))
    def test_send_message_swallows_network_errors(self, mock_post):
        self.assertFalse(services.send_message('привет'))

    def test_send_message_noop_when_not_configured(self):
        TelegramSettings.objects.all().delete()
        TelegramRecipient.objects.all().delete()
        with patch('notify.services.requests.post') as mock_post:
            self.assertFalse(services.send_message('привет'))
        mock_post.assert_not_called()

    @patch('notify.services.requests.post')
    def test_notify_new_order_sends_formatted_text(self, mock_post):
        mock_post.return_value = _fake_response({'ok': True})
        order = Order.objects.create(
            customer_name='Иван', customer_phone='+77070000000',
            total_price=self.product.price,
        )
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price=self.product.price)
        self.assertTrue(services.notify_new_order(order))
        for call in mock_post.call_args_list:
            self.assertIn('Wild Kilimanjaro', call.kwargs['json']['text'])

    def test_notify_new_order_noop_when_disabled(self):
        TelegramSettings.objects.all().delete()
        TelegramRecipient.objects.all().delete()
        order = Order.objects.create(customer_name='Иван', customer_phone='+77070000000')
        with patch('notify.services.requests.post') as mock_post:
            self.assertFalse(services.notify_new_order(order))
        mock_post.assert_not_called()

    @patch('notify.services.requests.post')
    def test_send_test_message_reports_sent_out_of_total(self, mock_post):
        def fake_post(url, timeout, json):
            ok = json['chat_id'] == '42'
            return _fake_response({'ok': ok})
        mock_post.side_effect = fake_post
        sent, total = services.send_test_message()
        self.assertEqual((sent, total), (1, 2))


class FindChatIdTests(TestCase):
    @patch('notify.services.requests.get')
    def test_finds_chat_id_from_last_update(self, mock_get):
        mock_get.return_value = _fake_response({
            'ok': True,
            'result': [{'message': {'chat': {'id': 555, 'first_name': 'Илья'}}}],
        })
        chat_id, title = services.find_chat_id('t-token')
        self.assertEqual(chat_id, '555')
        self.assertEqual(title, 'Илья')

    @patch('notify.services.requests.get')
    def test_raises_when_no_updates_yet(self, mock_get):
        mock_get.return_value = _fake_response({'ok': True, 'result': []})
        with self.assertRaises(services.TelegramError):
            services.find_chat_id('t-token')

    @patch('notify.services.requests.get')
    def test_raises_when_telegram_rejects_token(self, mock_get):
        mock_get.return_value = _fake_response({'ok': False, 'description': 'Unauthorized'}, status=401)
        with self.assertRaises(services.TelegramError):
            services.find_chat_id('bad-token')

    @patch('notify.services.requests.get', side_effect=requests.Timeout('timeout'))
    def test_raises_on_network_error(self, mock_get):
        with self.assertRaises(services.TelegramError):
            services.find_chat_id('t-token')


class TelegramSettingsAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser('owner', 'owner@example.com', 'pass12345')
        self.staff = User.objects.create_user('staff', 'staff@example.com', 'pass12345', is_staff=True)

    def test_changelist_redirects_to_singleton(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:notify_telegramsettings_changelist'))
        obj = TelegramSettings.load()
        self.assertRedirects(response, reverse('admin:notify_telegramsettings_change', args=[obj.pk]))

    def test_non_superuser_has_no_access(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('admin:notify_telegramsettings_changelist'))
        self.assertEqual(response.status_code, 302)  # редирект на логин/отказ

    def test_change_page_lists_existing_recipients_inline(self):
        obj = TelegramSettings.objects.create(bot_token='t-token')
        TelegramRecipient.objects.create(chat_id='111', label='Директор')
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:notify_telegramsettings_change', args=[obj.pk]))
        self.assertContains(response, '111')
        self.assertContains(response, 'Директор')

    @patch('notify.admin.services.find_chat_id')
    def test_find_chat_id_button_adds_a_new_recipient(self, mocked):
        mocked.return_value = ('777', 'Мой чат')
        obj = TelegramSettings.objects.create(bot_token='t-token')
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('admin:notify_telegramsettings_find_chat_id', args=[obj.pk]), follow=True,
        )
        self.assertTrue(TelegramRecipient.objects.filter(chat_id='777', label='Мой чат').exists())
        self.assertContains(response, '777')

    @patch('notify.admin.services.find_chat_id')
    def test_find_chat_id_does_not_duplicate_existing_recipient(self, mocked):
        mocked.return_value = ('777', 'Мой чат')
        TelegramRecipient.objects.create(chat_id='777', label='Мой чат')
        obj = TelegramSettings.objects.create(bot_token='t-token')
        self.client.force_login(self.superuser)
        self.client.post(reverse('admin:notify_telegramsettings_find_chat_id', args=[obj.pk]), follow=True)
        self.assertEqual(TelegramRecipient.objects.filter(chat_id='777').count(), 1)

    def test_find_chat_id_without_token_shows_error(self):
        obj = TelegramSettings.objects.create()
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('admin:notify_telegramsettings_find_chat_id', args=[obj.pk]), follow=True,
        )
        self.assertContains(response, 'Сначала сохраните токен бота')

    @patch('notify.admin.services.send_test_message', return_value=(2, 2))
    def test_send_test_message_when_ready(self, mocked):
        obj = TelegramSettings.objects.create(is_enabled=True, bot_token='t')
        TelegramRecipient.objects.create(chat_id='42')
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse('admin:notify_telegramsettings_send_test', args=[obj.pk]), follow=True,
        )
        mocked.assert_called_once()
        self.assertContains(response, 'отправлено')

    def test_send_test_message_when_not_ready(self):
        obj = TelegramSettings.objects.create()
        self.client.force_login(self.superuser)
        with patch('notify.admin.services.send_test_message') as mocked:
            response = self.client.post(
                reverse('admin:notify_telegramsettings_send_test', args=[obj.pk]), follow=True,
            )
        mocked.assert_not_called()
        self.assertContains(response, 'Заполните токен')
