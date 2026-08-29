"""Текущий HTTP-запрос в thread-local — чтобы сигналы и `record()` могли
достать IP/путь/User-Agent, не получая request явным аргументом отовсюду."""
import threading

_state = threading.local()


def set_current_request(request):
    _state.request = request


def clear_current_request():
    _state.request = None


def get_current_request():
    return getattr(_state, 'request', None)


def get_client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        # Первый адрес в цепочке — исходный клиент.
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None
