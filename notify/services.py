"""Уведомления о новых заказах в Telegram.

Бот и получатели настраиваются в админке («Настройки Telegram-уведомлений»):
владелец создаёт бота через @BotFather, вставляет токен; получателей
(TelegramRecipient) может быть несколько — каждый пишет боту любое
сообщение, затем кнопкой «Найти и добавить получателя» его чат подтягивается
в список. Отправка — обычный Bot API: POST /bot<token>/sendMessage, по
очереди каждому получателю.

  notify_new_order()     — уведомить всех получателей о новом заказе
  send_message()          — отправить текст всем получателям
  send_test_message()     — то же самое, но возвращает (отправлено, всего)
  find_chat_id()           — вытащить chat_id из последнего сообщения боту
"""
import logging

import requests

from .models import TelegramRecipient, TelegramSettings

logger = logging.getLogger('notify')

API_BASE = 'https://api.telegram.org'
_TIMEOUT = (5, 10)


class TelegramError(Exception):
    """Ошибка обращения к Telegram Bot API."""


def get_config():
    row = TelegramSettings.objects.filter(pk=1).first()
    return row if row and row.is_ready else None


def notifications_enabled():
    return get_config() is not None


def _kzt(amount):
    return f'{int(amount):,}'.replace(',', ' ') + ' ₸'


def _format_new_order(order):
    items = list(order.items.select_related('product').all())
    lines = [f'• {item.product.name} ×{item.quantity} — {_kzt(item.subtotal)}' for item in items]
    bouquets_total = sum(item.subtotal for item in items)

    if order.delivery_method == order.DeliveryMethod.PICKUP:
        delivery_line = 'Самовывоз'
    elif order.delivery_zone and order.delivery_price:
        delivery_line = _kzt(order.delivery_price)
    elif order.delivery_zone:
        delivery_line = 'по согласованию с менеджером'
    else:
        delivery_line = 'уточняется'

    return (
        f'🌸 <b>Новый заказ №{order.pk}</b>\n\n'
        + ('\n'.join(lines) if lines else '—')
        + '\n\n'
        f'💳 Оплата: {order.get_status_display()}\n'
        f'Букет: {_kzt(bouquets_total)}\n'
        f'Доставка: {delivery_line}\n'
        f'<b>Итого: {_kzt(order.total_price)}</b>\n\n'
        f'👤 {order.customer_name}, {order.customer_phone}'
    )


def _send_one(bot_token, chat_id, text):
    url = f'{API_BASE}/bot{bot_token}/sendMessage'
    try:
        resp = requests.post(
            url, timeout=_TIMEOUT,
            json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        logger.exception('telegram: не удалось отправить сообщение в чат %s', chat_id)
        return False
    if not data.get('ok'):
        logger.error('telegram: sendMessage отказал (чат %s): %s', chat_id, data)
        return False
    return True


def _send_to_all(bot_token, text):
    """Возвращает (сколько получателей получили сообщение, всего получателей)."""
    chat_ids = list(TelegramRecipient.objects.values_list('chat_id', flat=True))
    sent = sum(_send_one(bot_token, chat_id, text) for chat_id in chat_ids)
    return sent, len(chat_ids)


def send_message(text):
    """Отправляет текст всем настроенным получателям. Ничего не бросает —
    возвращает True, если сообщение ушло хотя бы одному (ошибки только
    логируются: уведомление не должно ронять оформление заказа или админку)."""
    cfg = get_config()
    if cfg is None:
        return False
    sent, _total = _send_to_all(cfg.bot_token, text)
    return sent > 0


def send_test_message():
    """Как send_message, но возвращает (отправлено, всего) — для админки,
    чтобы показать точнее, чем просто True/False."""
    cfg = get_config()
    if cfg is None:
        return 0, 0
    return _send_to_all(cfg.bot_token, '✅ Тестовое сообщение от Blackpepper Flower Bar.')


def notify_new_order(order):
    """Уведомляет всех получателей о новом заказе. Молча ничего не делает,
    если уведомления не настроены/выключены."""
    if not notifications_enabled():
        return False
    return send_message(_format_new_order(order))


def find_chat_id(bot_token):
    """Ищет chat_id в последнем сообщении, отправленном боту (getUpdates).
    Возвращает (chat_id, название чата для показа в админке)."""
    url = f'{API_BASE}/bot{bot_token}/getUpdates'
    try:
        resp = requests.get(url, timeout=_TIMEOUT, params={'limit': 1, 'offset': -1})
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f'сеть: {exc}') from exc
    if not data.get('ok'):
        raise TelegramError(data.get('description') or 'неизвестная ошибка Telegram API')
    results = data.get('result') or []
    if not results:
        raise TelegramError(
            'Telegram ничего не прислал — сначала напишите боту любое сообщение в '
            'Telegram и повторите'
        )
    update = results[-1]
    chat = (
        (update.get('message') or {}).get('chat')
        or (update.get('channel_post') or {}).get('chat')
        or {}
    )
    chat_id = chat.get('id')
    if chat_id is None:
        raise TelegramError('не удалось найти chat ID в ответе Telegram')
    title = chat.get('title') or chat.get('username') or chat.get('first_name') or str(chat_id)
    return str(chat_id), title
