import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html

from .models import AuditEvent
from .services import record

# Действия, которые видит только владелец — директору в журнале их не показываем.
_SYSTEM_ACTIONS = {AuditEvent.Action.SYSTEM}


class EventKindFilter(admin.SimpleListFilter):
    """Просмотры страниц (`view`) — это ~95% строк. По умолчанию скрываем их,
    оставляя «значимые» события; при желании можно посмотреть отдельно или всё."""

    title = 'тип событий'
    parameter_name = 'kind'

    def lookups(self, request, model_admin):
        return (
            ('significant', 'Значимые'),
            ('views', 'Просмотры страниц'),
            ('all', 'Все события'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'views':
            return queryset.filter(action=AuditEvent.Action.VIEW)
        if value == 'all':
            return queryset
        return queryset.exclude(action=AuditEvent.Action.VIEW)

    def choices(self, changelist):
        current = self.value() or 'significant'
        for lookup, title in self.lookup_choices:
            yield {
                'selected': current == str(lookup),
                'query_string': changelist.get_query_string({self.parameter_name: lookup}),
                'display': title,
            }


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        'timestamp', 'actor_cell', 'action', 'outcome', 'target_cell', 'changes_summary', 'ip_address',
    )
    # «Тип объекта» намеренно не выводим в фильтры — это ~20 пунктов, включая
    # системные типы; рельс фильтров становится нечитаемым. Фильтрация по
    # конкретному объекту доступна по ссылке «История» с его карточки.
    list_filter = (EventKindFilter, 'action', 'actor_role', 'outcome')
    date_hierarchy = 'timestamp'
    search_fields = ('actor_repr', 'target_repr', 'ip_address', 'request_path')
    actions = ['export_csv']

    def lookup_allowed(self, lookup, value, request=None):
        # Разрешаем адресную фильтрацию по объекту (ссылка «История» с карточки),
        # хотя этих полей нет в сайдбаре фильтров.
        if lookup in ('target_content_type__id__exact', 'target_object_id', 'target_object_id__exact'):
            return True
        return super().lookup_allowed(lookup, value, request)

    # --- только чтение, полностью -----------------------------------------

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        user = request.user
        return user.is_active and (user.is_superuser or user.has_perm('audit.view_auditevent'))

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields] + ['changes', 'context']

    # --- ролевая видимость записей --------------------------------------

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('actor', 'target_content_type')
        if request.user.is_superuser:
            return qs
        # Директор: не видит действия владельца и системные события (Q1 — «нет»).
        return qs.exclude(actor_role=AuditEvent.Role.OWNER).exclude(action__in=_SYSTEM_ACTIONS)

    # --- ячейки списка -------------------------------------------------

    @admin.display(description='Кто', ordering='actor_repr')
    def actor_cell(self, obj):
        label = dict(AuditEvent.Role.choices).get(obj.actor_role, obj.actor_role)
        return format_html('{} <span style="opacity:.6">· {}</span>', obj.actor_repr or '—', label)

    @admin.display(description='Объект')
    def target_cell(self, obj):
        if obj.target_repr:
            return obj.target_repr
        if obj.context and obj.context.get('model'):
            return obj.context['model']
        return '—'

    @admin.display(description='Что изменилось')
    def changes_summary(self, obj):
        if obj.action == AuditEvent.Action.BULK_ACTION and obj.context:
            return f'{obj.context.get("action", "действие")} · объектов: {obj.context.get("count", "?")}'
        if not obj.changes:
            return '—'
        return ', '.join(obj.changes.keys())

    # --- выгрузка -----------------------------------------------------

    @admin.action(description='Выгрузить выбранное в CSV')
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="audit.csv"'
        response.write('﻿')  # BOM — чтобы Excel открыл кириллицу
        writer = csv.writer(response)
        writer.writerow(['Время', 'Кто', 'Роль', 'Действие', 'Результат', 'Объект', 'Изменения', 'IP', 'Путь'])
        for e in queryset:
            writer.writerow([
                e.timestamp.strftime('%Y-%m-%d %H:%M:%S'), e.actor_repr, e.get_actor_role_display(),
                e.get_action_display(), e.get_outcome_display(), e.target_repr,
                '; '.join((e.changes or {}).keys()), e.ip_address or '', e.request_path,
            ])
        record(action=AuditEvent.Action.EXPORT, actor=request.user, request=request,
               context={'count': queryset.count(), 'format': 'csv'})
        return response
