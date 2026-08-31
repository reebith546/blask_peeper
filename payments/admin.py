from django import forms
from django.conf import settings
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from audit.admin_mixins import AuditModelAdmin

from . import gateway
from .models import Payment, PaymentSettings


@admin.register(Payment)
class PaymentAdmin(AuditModelAdmin, admin.ModelAdmin):
    list_display = ('id', 'order', 'amount', 'currency', 'status', 'invoice_id', 'paid_at', 'created_at')
    list_filter = ('status', 'currency')
    search_fields = ('order__id', 'invoice_id', 'external_id')
    readonly_fields = (
        'order', 'amount', 'currency', 'invoice_id', 'external_id',
        'form_url', 'raw_callback_data', 'paid_at', 'created_at', 'updated_at',
    )
    actions = ['recheck_status']

    def has_add_permission(self, request):
        # Платежи создаются только из чекаута, не руками.
        return False

    @admin.action(description='Проверить статус в платёжном шлюзе')
    def recheck_status(self, request, queryset):
        checked = 0
        for payment in queryset:
            try:
                gateway.check_payment(payment, source='admin')
                checked += 1
            except gateway.PaymentGatewayError as exc:
                self.message_user(request, f'Платёж {payment.invoice_id}: {exc}', level='error')
        if checked:
            self.message_user(request, f'Проверено платежей: {checked}.')


class PaymentSettingsForm(forms.ModelForm):
    # Секрет не отдаём обратно в форму — только приём. Пусто = не менять.
    api_secret = forms.CharField(
        label='API Secret (пароль API и ключ подписи)',
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='Оставьте пустым, чтобы сохранить текущее значение.',
    )

    class Meta:
        model = PaymentSettings
        fields = ('is_enabled', 'public_id', 'api_secret', 'api_base', 'currency')

    def clean_api_secret(self):
        value = self.cleaned_data.get('api_secret', '')
        if not value and self.instance and self.instance.pk:
            return self.instance.api_secret  # ничего не ввели — оставляем как было
        return value


@admin.register(PaymentSettings)
class PaymentSettingsAdmin(AuditModelAdmin, admin.ModelAdmin):
    """Реквизиты платёжного шлюза — заполняет владелец. Только для superuser."""

    form = PaymentSettingsForm
    # Секрет исключаем из diff «Журнала действий», чтобы не светить его значение.
    audit_exclude_fields = ('api_secret',)
    readonly_fields = ('callback_url', 'status_note', 'updated_at')
    fieldsets = (
        ('Приём оплаты', {'fields': ('is_enabled', 'status_note')}),
        ('Реквизиты из личного кабинета TipTop Pay', {
            'fields': ('public_id', 'api_secret', 'api_base', 'currency'),
        }),
        ('Для кабинета TipTop Pay', {
            'fields': ('callback_url',),
            'description': 'В личном кабинете TipTop Pay в настройках уведомлений '
                           '(webhook) укажите эти адреса — по одному на тип события. '
                           'Схема аутентификации уведомлений — HMAC.',
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
        obj = PaymentSettings.load()
        return redirect(reverse('admin:payments_paymentsettings_change', args=[obj.pk]))

    @admin.display(description='Адреса webhook для кабинета TipTop Pay')
    def callback_url(self, obj):
        from django.utils.html import format_html
        host = next((h for h in settings.ALLOWED_HOSTS if h not in ('*', 'localhost', '127.0.0.1')),
                    'ваш-домен')
        base = f'https://{host}/payments/callback/'
        return format_html(
            'Pay: <code>{}?type=pay</code><br>'
            'Fail: <code>{}?type=fail</code><br>'
            'Refund: <code>{}?type=refund</code><br>'
            'Check (если используете): <code>{}?type=check</code>',
            base, base, base, base,
        )

    @admin.display(description='Состояние')
    def status_note(self, obj):
        if gateway.payments_enabled():
            return 'Онлайн-оплата активна.'
        if obj and obj.is_enabled and not (obj.public_id and obj.api_secret):
            return 'Флаг включён, но не заполнены оба реквизита — оплата не работает.'
        return 'Онлайн-оплата выключена. Заказы принимаются без предоплаты.'
