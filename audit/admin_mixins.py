"""`AuditModelAdmin` — примесь к ModelAdmin, которая пишет в журнал действий
всё, что делается через админку: создание, изменение (с разбором «было/стало»),
удаление и массовые действия.

Штатный `django.contrib.admin.LogEntry` («Последние действия» в сайдбаре)
продолжает работать — мы вызываем `super()` и дополнительно кладём событие
в наш журнал.
"""
from functools import wraps

from django.contrib.contenttypes.models import ContentType
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.encoding import force_str

from .models import AuditEvent
from .services import SENSITIVE_ACCOUNT_FIELDS, record

_PASSWORD_MARKERS = ('password', 'парол')


def _repr_value(value):
    if value is None:
        return None
    return force_str(value)[:300]


class AuditModelAdmin:
    def save_model(self, request, obj, form, change):
        # До сохранения снимаем старые значения изменённых простых полей.
        diff = {}
        if change and obj.pk is not None:
            model = type(obj)
            try:
                old = model._default_manager.get(pk=obj.pk)
            except model.DoesNotExist:
                old = None
            if old is not None:
                for field_name in getattr(form, 'changed_data', []):
                    try:
                        field = model._meta.get_field(field_name)
                    except Exception:
                        continue
                    if field.many_to_many:
                        continue  # M2M разбираем в save_related
                    old_v = _repr_value(getattr(old, field.attname, getattr(old, field_name, None)))
                    new_v = _repr_value(getattr(obj, field.attname, getattr(obj, field_name, None)))
                    if old_v != new_v:
                        diff[field_name] = [old_v, new_v]
        self._audit_diff = diff
        self._audit_old_is_active = None
        if change and obj.pk is not None and hasattr(obj, 'is_active'):
            try:
                self._audit_old_is_active = type(obj)._default_manager.values_list(
                    'is_active', flat=True,
                ).get(pk=obj.pk)
            except Exception:
                self._audit_old_is_active = None
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        before = {}
        instance = form.instance
        if change:
            changed = set(getattr(form, 'changed_data', []))
            for field in instance._meta.many_to_many:
                if field.name in changed:
                    before[field.name] = sorted(
                        force_str(x) for x in getattr(instance, field.name).all()
                    )
        super().save_related(request, form, formsets, change)
        for name, old_list in before.items():
            new_list = sorted(force_str(x) for x in getattr(instance, name).all())
            if old_list != new_list:
                self._audit_diff[name] = [old_list, new_list]

    # --- перехват штатных точек логирования ------------------------------

    def log_addition(self, request, obj, message):
        entry = super().log_addition(request, obj, message)
        self._audit_safe(
            action=AuditEvent.Action.CREATE, actor=request.user, target=obj,
            request=request, context=self._account_context(obj),
        )
        return entry

    def log_change(self, request, obj, message):
        entry = super().log_change(request, obj, message)
        diff = getattr(self, '_audit_diff', {}) or {}

        if not diff and self._looks_like_password_change(message):
            self._audit_safe(
                action=AuditEvent.Action.PASSWORD_CHANGE, actor=request.user,
                target=obj, request=request,
            )
            return entry

        # Отдельные события активации/деактивации учётки — их удобно фильтровать.
        old_active = getattr(self, '_audit_old_is_active', None)
        if 'is_active' in diff and old_active is not None:
            now_active = getattr(obj, 'is_active', None)
            if now_active is False and old_active is True:
                self._audit_safe(action=AuditEvent.Action.DEACTIVATE, actor=request.user,
                                 target=obj, request=request)
            elif now_active is True and old_active is False:
                self._audit_safe(action=AuditEvent.Action.ACTIVATE, actor=request.user,
                                 target=obj, request=request)

        self._audit_safe(
            action=AuditEvent.Action.UPDATE, actor=request.user, target=obj,
            changes=diff or None, request=request, context=self._account_context(obj, diff),
        )
        return entry

    def history_view(self, request, object_id, extra_context=None):
        # Кнопка «История» на карточке объекта ведёт в общий «Журнал действий»,
        # отфильтрованный по этому объекту, — чтобы не было двух параллельных
        # историй. Кому журнал недоступен (продавцы) — показываем штатную.
        if request.user.has_perm('audit.view_auditevent'):
            ct = ContentType.objects.get_for_model(self.model, for_concrete_model=True)
            url = reverse('admin:audit_auditevent_changelist')
            return redirect(
                f'{url}?kind=all'
                f'&target_content_type__id__exact={ct.pk}'
                f'&target_object_id={object_id}'
            )
        return super().history_view(request, object_id, extra_context)

    def log_deletion(self, request, obj, object_repr):
        entry = super().log_deletion(request, obj, object_repr)
        self._audit_safe(
            action=AuditEvent.Action.DELETE, actor=request.user, target=obj,
            target_repr=object_repr, request=request,
        )
        return entry

    # --- массовые действия ---------------------------------------------

    def get_action(self, action):
        result = super().get_action(action)
        if result is None:
            return None
        func, name, description = result
        if name == 'delete_selected':
            # У встроенного удаления уже есть пер-объектный log_deletion.
            return result
        return (self._wrap_action(func, name), name, description)

    def _wrap_action(self, func, name):
        @wraps(func)
        def wrapper(modeladmin, request, queryset):
            ids = list(queryset.values_list('pk', flat=True))
            model_label = queryset.model._meta.label
            response = func(modeladmin, request, queryset)
            record(
                action=AuditEvent.Action.BULK_ACTION, actor=request.user, request=request,
                context={'action': name, 'model': model_label,
                         'count': len(ids), 'ids': [str(i) for i in ids[:200]]},
            )
            return response
        return wrapper

    # --- вспомогательное ---------------------------------------------------

    def _audit_safe(self, **kwargs):
        try:
            record(**kwargs)
        except Exception:  # pragma: no cover
            pass

    @staticmethod
    def _looks_like_password_change(message):
        text = force_str(message).lower()
        return any(marker in text for marker in _PASSWORD_MARKERS)

    @staticmethod
    def _account_context(obj, diff=None):
        if not hasattr(obj, 'is_staff'):
            return None
        ctx = {'account': True}
        if diff:
            sensitive = sorted(set(diff) & SENSITIVE_ACCOUNT_FIELDS)
            if sensitive:
                ctx['sensitive_fields'] = sensitive
        return ctx
