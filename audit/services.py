"""Единая точка записи в журнал действий: `record(...)`.

Любая ошибка записи журнала гасится и не ломает основное действие
пользователя — журнал вторичен по отношению к работе магазина.
"""
import logging

from django.contrib.contenttypes.models import ContentType

from .context import get_client_ip, get_current_request
from .models import AuditEvent

logger = logging.getLogger('audit')

# Поля учётной записи, изменение которых считаем чувствительным.
SENSITIVE_ACCOUNT_FIELDS = {'groups', 'user_permissions', 'is_staff', 'is_superuser', 'is_active', 'password'}


def role_of(user):
    """Роль пользователя на момент действия (снимок для журнала)."""
    from accounts.models import DIRECTOR_GROUP_NAME

    if user is None or not getattr(user, 'is_authenticated', False):
        return AuditEvent.Role.ANON
    if user.is_superuser:
        return AuditEvent.Role.OWNER
    if user.groups.filter(name=DIRECTOR_GROUP_NAME).exists():
        return AuditEvent.Role.DIRECTOR
    return AuditEvent.Role.SELLER


def record(*, action, actor=None, actor_role=None, target=None, target_repr='',
           changes=None, context=None, outcome=AuditEvent.Outcome.SUCCESS, request=None):
    """Создаёт запись журнала. Никогда не бросает исключение наружу."""
    try:
        request = request if request is not None else get_current_request()

        if actor is None and request is not None:
            candidate = getattr(request, 'user', None)
            if candidate is not None and getattr(candidate, 'is_authenticated', False):
                actor = candidate

        if actor_role is None:
            actor_role = role_of(actor)

        ct = oid = None
        if target is not None:
            ct = ContentType.objects.get_for_model(target.__class__)
            oid = str(getattr(target, 'pk', '') or '')
            if not target_repr:
                target_repr = str(target)

        ip = user_agent = path = method = ''
        if request is not None:
            ip = get_client_ip(request)
            user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:400]
            path = request.path[:400]
            method = request.method or ''

        AuditEvent.objects.create(
            actor=actor if (actor is not None and getattr(actor, 'pk', None)) else None,
            actor_repr=(_user_repr(actor))[:200],
            actor_role=actor_role,
            action=action,
            outcome=outcome,
            target_content_type=ct,
            target_object_id=(oid or '')[:64],
            target_repr=(target_repr or '')[:300],
            changes=changes or None,
            context=context or None,
            ip_address=ip or None,
            user_agent=user_agent,
            request_path=path,
            request_method=method,
        )
    except Exception:  # pragma: no cover - журнал не должен ронять запросы
        logger.exception('Не удалось записать событие в журнал действий')


def _user_repr(user):
    if user is None:
        return ''
    name = (getattr(user, 'get_full_name', lambda: '')() or '').strip()
    username = getattr(user, 'username', '') or getattr(user, 'get_username', lambda: '')()
    if name and username:
        return f'{name} ({username})'
    return name or username or str(user)
