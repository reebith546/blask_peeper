"""Уведомления о новых заказах в Telegram.

Бот и получатель настраиваются в админке («Настройки Telegram-уведомлений»):
владелец создаёт бота через @BotFather, вставляет токен, пишет этому боту
любое сообщение и нажимает «Найти chat ID» — дальше находить получателя
вручную не нужно. Отправка — обычный Bot API: POST /bot<token>/sendMessage.

  notify_new_order()     — уведомить о только что созданном заказе
  send_message()          — отправить произвольный текст в настроенный чат
  find_chat_id()           — вытащить chat_id из последнего сообщения боту
"""
import logging

import requests

from .models import TelegramSettings

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


def send_message(text):
    """Отправляет текст в настроенный чат. Ничего не бросает — возвращает
    True/False, ошибки только логируются (уведомление не должно ронять
    оформление заказа или админку)."""
    cfg = get_config()
    if cfg is None:
        return False
    url = f'{API_BASE}/bot{cfg.bot_token}/sendMessage'
    try:
        resp = requests.post(
            url, timeout=_TIMEOUT,
            json={'chat_id': cfg.chat_id, 'text': text, 'parse_mode': 'HTML'},
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        logger.exception('telegram: не удалось отправить сообщение')
        return False
    if not data.get('ok'):
        logger.error('telegram: sendMessage отказал: %s', data)
        return False
    return True


def notify_new_order(order):
    """Уведомляет о новом заказе. Молча ничего не делает, если уведомления
    не настроены/выключены."""
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
