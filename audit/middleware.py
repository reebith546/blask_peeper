"""Middleware журнала действий:

1. кладёт текущий request в thread-local (нужно сигналам и `record()`);
2. по Q5 — пишет в журнал каждый заход в админку (метод, путь, статус).

Технические ресурсы (jsi18n, статика, autocomplete-подсказки) не логируем,
чтобы не засорять журнал служебными запросами каждой страницы.
"""
from .context import clear_current_request, set_current_request
from .models import AuditEvent
from .services import record

ADMIN_PREFIX = '/admin/'
_SKIP_SUFFIXES = ('/jsi18n/', '/autocomplete/')
_SKIP_CONTAINS = ('/admin/js/', '/static/')


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            response = self.get_response(request)
            self._maybe_log_visit(request, response)
            return response
        finally:
            clear_current_request()

    def _maybe_log_visit(self, request, response):
        path = request.path or ''
        if not path.startswith(ADMIN_PREFIX):
            return
        if request.method == 'OPTIONS':
            return
        if path.endswith(_SKIP_SUFFIXES) or any(part in path for part in _SKIP_CONTAINS):
            return

        status = getattr(response, 'status_code', 0)
        if status in (301, 302) and ADMIN_PREFIX + 'login/' in (response.get('Location', '') or ''):
            # Редирект неавторизованного на форму входа — не отдельное событие.
            return

        if status == 403:
            outcome = AuditEvent.Outcome.DENIED
        elif status >= 500:
            outcome = AuditEvent.Outcome.ERROR
        else:
            outcome = AuditEvent.Outcome.SUCCESS

        record(
            action=AuditEvent.Action.VIEW,
            request=request,
            outcome=outcome,
            context={'status': status},
        )
