from django import forms
from django.contrib import admin, messages
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from audit.admin_mixins import AuditModelAdmin

from . import services
from .models import TelegramSettings


class TelegramSettingsForm(forms.ModelForm):
    # Токен не отдаём обратно в форму — только приём. Пусто = не менять.
    bot_token = forms.CharField(
        label='Токен бота',
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='Оставьте пустым, чтобы сохранить текущее значение.',
    )

    class Meta:
        model = TelegramSettings
        fields = ('is_enabled', 'bot_token', 'chat_id')

    def clean_bot_token(self):
        value = self.cleaned_data.get('bot_token', '')
        if not value and self.instance and self.instance.pk:
            return self.instance.bot_token  # ничего не ввели — оставляем как было
        return value


@admin.register(TelegramSettings)
class TelegramSettingsAdmin(AuditModelAdmin, admin.ModelAdmin):
    """Реквизиты Telegram-бота — заполняет владелец. Только для superuser."""

    form = TelegramSettingsForm
    audit_exclude_fields = ('bot_token',)
    readonly_fields = ('status_note', 'find_chat_id_button', 'send_test_button', 'updated_at')
    fieldsets = (
        ('Уведомления о новых заказах', {'fields': ('is_enabled', 'status_note')}),
        ('Бот', {
            'fields': ('bot_token', 'chat_id', 'find_chat_id_button', 'send_test_button'),
            'description': (
                '1. Создайте бота через <a href="https://t.me/BotFather" target="_blank" '
                'rel="noopener">@BotFather</a> командой /newbot, вставьте сюда токен и '
                'сохраните.<br>'
                '2. Напишите этому боту в Telegram любое сообщение (например «привет»).<br>'
                '3. Нажмите «Найти chat ID» — он подставится сам. Кнопка «Тестовое '
                'сообщение» проверит, что всё работает.'
            ),
        }),
        (None, {'fields': ('updated_at',)}),
    )

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Синглтон — сразу открываем единственную запись.
        obj = TelegramSettings.load()
        return redirect(reverse('admin:notify_telegramsettings_change', args=[obj.pk]))

    def get_urls(self):
        custom = [
            path(
                '<int:object_id>/find-chat-id/',
                self.admin_site.admin_view(require_POST(self.find_chat_id_view)),
                name='notify_telegramsettings_find_chat_id',
            ),
            path(
                '<int:object_id>/send-test/',
                self.admin_site.admin_view(require_POST(self.send_test_view)),
                name='notify_telegramsettings_send_test',
            ),
        ]
        return custom + super().get_urls()

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        # Нужен в find_chat_id_button/send_test_button, чтобы вывести CSRF-токен
        # для их мини-форм (кнопки шлют POST, а не просто переходят по ссылке).
        self._request = request
        return super().changeform_view(request, object_id, form_url, extra_context)

    def find_chat_id_view(self, request, object_id):
        obj = TelegramSettings.load()
        if not obj.bot_token:
            messages.error(request, 'Сначала сохраните токен бота.')
        else:
            try:
                chat_id, title = services.find_chat_id(obj.bot_token)
            except services.TelegramError as exc:
                messages.error(request, f'Не удалось найти chat ID: {exc}')
            else:
                obj.chat_id = chat_id
                obj.save(update_fields=['chat_id', 'updated_at'])
                messages.success(request, f'Chat ID найден и сохранён: «{title}» ({chat_id}).')
        return redirect(reverse('admin:notify_telegramsettings_change', args=[obj.pk]))

    def send_test_view(self, request, object_id):
        obj = TelegramSettings.load()
        if not obj.is_ready:
            messages.error(request, 'Заполните токен, chat ID и включите уведомления.')
        elif services.send_message('✅ Тестовое сообщение от Blackpepper Flower Bar.'):
            messages.success(request, 'Тестовое сообщение отправлено — проверьте Telegram.')
        else:
            messages.error(request, 'Не удалось отправить сообщение — подробности в логах сервера.')
        return redirect(reverse('admin:notify_telegramsettings_change', args=[obj.pk]))

    def _action_button(self, obj, url_name, label):
        if not obj or not obj.pk:
            return '—'
        request = getattr(self, '_request', None)
        if request is None:
            return '—'
        url = reverse(url_name, args=[obj.pk])
        return format_html(
            '<form method="post" action="{}" style="display:inline">'
            '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
            '<button type="submit" class="button">{}</button>'
            '</form>',
            url, get_token(request), label,
        )

    @admin.display(description='Найти получателя')
    def find_chat_id_button(self, obj):
        return self._action_button(
            obj, 'admin:notify_telegramsettings_find_chat_id',
            'Найти chat ID по последнему сообщению боту',
        )

    @admin.display(description='Проверка')
    def send_test_button(self, obj):
        return self._action_button(
            obj, 'admin:notify_telegramsettings_send_test', 'Отправить тестовое сообщение',
        )

    @admin.display(description='Состояние')
    def status_note(self, obj):
        if services.notifications_enabled():
            return 'Уведомления в Telegram активны.'
        if obj and obj.is_enabled and not (obj.bot_token and obj.chat_id):
            return 'Флаг включён, но не заполнены токен и chat ID — уведомления не отправляются.'
        return 'Уведомления выключены.'
