"""Аутентификация в журнале: вход, выход, неудачная попытка входа."""
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditEvent
from .services import record, role_of


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    record(action=AuditEvent.Action.LOGIN, actor=user, request=request)


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    record(action=AuditEvent.Action.LOGOUT, actor=user, request=request)


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    username = credentials.get('username') or credentials.get('email') or '—'
    # Если такой пользователь существует — сохраняем его роль снимком,
    # иначе это просто попытка с неизвестным логином.
    actor = get_user_model().objects.filter(username=username).first()
    record(
        action=AuditEvent.Action.LOGIN_FAILED,
        actor=actor,
        actor_role=role_of(actor) if actor else AuditEvent.Role.ANON,
        outcome=AuditEvent.Outcome.DENIED,
        request=request,
        context={'username': username},
    )
