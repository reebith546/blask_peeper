from django import forms
from django.contrib import admin, messages
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from audit.admin_mixins import AuditModelAdmin

from . import services
from .models import TelegramRecipient, TelegramSettings


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
        fields = ('is_enabled', 'bot_token')

    def clean_bot_token(self):
        value = self.cleaned_data.get('bot_token', '')
        if not value and self.instance and self.instance.pk:
            return self.instance.bot_token  # ничего не ввели — оставляем как было
        return value


class TelegramRecipientInline(admin.TabularInline):
    """Кому слать уведомления — можно добавить сколько угодно чатов вручную,
    либо кнопкой «Найти и добавить получателя» выше подтянуть следующий
    чат по последнему сообщению боту."""

    model = TelegramRecipient
    extra = 1
    fields = ('chat_id', 'label', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(TelegramSettings)
class TelegramSettingsAdmin(AuditModelAdmin, admin.ModelAdmin):
    """Реквизиты Telegram-бота — заполняет владелец. Только для superuser."""

    form = TelegramSettingsForm
    audit_exclude_fields = ('bot_token',)
    inlines = [TelegramRecipientInline]
    readonly_fields = ('status_note', 'find_chat_id_button', 'send_test_button', 'updated_at')
    fieldsets = (
        ('Уведомления о новых заказах', {'fields': ('is_enabled', 'status_note')}),
        ('Бот', {
            'fields': ('bot_token', 'find_chat_id_button', 'send_test_button'),
            'description': (
                '1. Создайте бота через <a href="https://t.me/BotFather" target="_blank" '
                'rel="noopener">@BotFather</a> командой /newbot, вставьте сюда токен и '
                'сохраните.<br>'
                '2. Чтобы добавить получателя — пусть он напишет этому боту в Telegram '
                'любое сообщение (например «привет»), затем нажмите «Найти и добавить '
                'получателя»: чат появится в списке ниже. Так можно добавить сколько '
                'угодно получателей (владелец, менеджеры, отдельная группа) — просто '
                'пусть каждый по очереди напишет боту, и после каждого нажимайте кнопку '
                'заново. Chat ID группы можно и вписать вручную в список ниже.<br>'
                '3. Кнопка «Тестовое сообщение» разошлёт проверочный текст всем '
                'получателям из списка.'
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
                recipient, created = TelegramRecipient.objects.get_or_create(
                    chat_id=chat_id, defaults={'label': title},
                )
                if created:
                    messages.success(request, f'Добавлен новый получатель: «{title}» ({chat_id}).')
                else:
                    messages.info(request, f'«{title}» ({chat_id}) уже есть в списке получателей.')
        return redirect(reverse('admin:notify_telegramsettings_change', args=[obj.pk]))

    def send_test_view(self, request, object_id):
        obj = TelegramSettings.load()
        if not obj.is_ready:
            messages.error(request, 'Заполните токен, добавьте хотя бы одного получателя и включите уведомления.')
        else:
            sent, total = services.send_test_message()
            if sent == total:
                messages.success(request, f'Тестовое сообщение отправлено всем получателям ({sent} из {total}).')
            elif sent:
                messages.warning(request, f'Тестовое сообщение отправлено не всем: {sent} из {total}. Подробности — в логах сервера.')
            else:
                messages.error(request, 'Не удалось отправить сообщение ни одному получателю — подробности в логах сервера.')
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

    @admin.display(description='Добавить получателя')
    def find_chat_id_button(self, obj):
        return self._action_button(
            obj, 'admin:notify_telegramsettings_find_chat_id',
            'Найти и добавить получателя по последнему сообщению боту',
        )

    @admin.display(description='Проверка')
    def send_test_button(self, obj):
        return self._action_button(
            obj, 'admin:notify_telegramsettings_send_test', 'Отправить тестовое сообщение всем',
        )

    @admin.display(description='Состояние')
    def status_note(self, obj):
        count = TelegramRecipient.objects.count()
        if services.notifications_enabled():
            word = _pluralize_recipients(count)
            return f'Уведомления в Telegram активны — получателей: {count} {word}.'
        if obj and obj.is_enabled and not obj.bot_token:
            return 'Флаг включён, но не сохранён токен бота — уведомления не отправляются.'
        if obj and obj.is_enabled and obj.bot_token and count == 0:
            return 'Флаг включён и токен сохранён, но нет ни одного получателя — добавьте хотя бы одного.'
        return 'Уведомления выключены.'


def _pluralize_recipients(count):
    n = count % 100
    if 11 <= n <= 14:
        return 'получателей'
    n = n % 10
    if n == 1:
        return 'получатель'
    if 2 <= n <= 4:
        return 'получателя'
    return 'получателей'
