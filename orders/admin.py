from django.contrib import admin
from django.utils.text import Truncator

from audit.admin_mixins import AuditModelAdmin
from payments.models import Payment

from .models import Order, OrderItem


def _kzt(amount):
    return f'{int(amount):,}'.replace(',', ' ') + ' ₸'


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ('product', 'quantity', 'price', 'subtotal_display')
    readonly_fields = ('price', 'subtotal_display')

    @admin.display(description='Сумма')
    def subtotal_display(self, obj):
        if not obj.pk:
            return '—'
        return _kzt(obj.subtotal)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    fields = ('status', 'amount', 'currency', 'invoice_id', 'paid_at', 'created_at')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(AuditModelAdmin, admin.ModelAdmin):
    list_display = (
        'id', 'customer_name', 'customer_phone', 'address_short', 'status', 'payment_state',
        'total_price_display', 'delivery_date', 'created_at',
    )
    list_editable = ('status',)
    list_filter = ('status', 'delivery_zone', 'created_at')
    search_fields = ('id', 'customer_name', 'customer_phone', 'customer_email', 'delivery_address')
    readonly_fields = ('created_at', 'updated_at', 'total_price')
    inlines = [OrderItemInline, PaymentInline]
    date_hierarchy = 'created_at'
    actions = ['mark_as_paid', 'mark_as_processing', 'mark_as_delivered']

    fieldsets = (
        ('Статус', {'fields': ('status',)}),
        ('Клиент', {'fields': ('customer_name', 'customer_phone', 'customer_email')}),
        ('Доставка', {'fields': (
            'delivery_zone', 'delivery_address', 'delivery_price',
            'delivery_date', 'delivery_time',
        )}),
        ('Открытка и комментарий', {'fields': ('card_text', 'comment')}),
        ('Итоги', {'fields': ('total_price', 'created_at', 'updated_at')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('payments')

    @admin.display(description='Сумма, ₸', ordering='total_price')
    def total_price_display(self, obj):
        return _kzt(obj.total_price)

    @admin.display(description='Адрес', ordering='delivery_address')
    def address_short(self, obj):
        # В списке — коротко (полный адрес и комментарий видно в карточке заказа).
        return Truncator(obj.delivery_address).chars(38) or '—'

    @admin.display(description='Оплата')
    def payment_state(self, obj):
        payment = obj.payments.order_by('-created_at').first()
        return payment.get_status_display() if payment else '—'

    @admin.action(description='Отметить как «Оплачен»')
    def mark_as_paid(self, request, queryset):
        queryset.update(status=Order.Status.PAID)

    @admin.action(description='Отметить как «Собирается»')
    def mark_as_processing(self, request, queryset):
        queryset.update(status=Order.Status.PROCESSING)

    @admin.action(description='Отметить как «Доставлен»')
    def mark_as_delivered(self, request, queryset):
        queryset.update(status=Order.Status.DELIVERED)
