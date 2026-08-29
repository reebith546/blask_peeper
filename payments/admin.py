from django.contrib import admin

from audit.admin_mixins import AuditModelAdmin

from . import gateway
from .models import Payment


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
