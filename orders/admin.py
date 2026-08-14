from django.contrib import admin
from django.utils.html import format_html

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


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'customer_name', 'customer_phone', 'status',
        'total_price_display', 'delivery_date', 'created_at',
    )
    list_editable = ('status',)
    list_filter = ('status', 'delivery_zone', 'created_at')
    search_fields = ('id', 'customer_name', 'customer_phone', 'customer_email')
    readonly_fields = ('created_at', 'updated_at', 'total_price')
    inlines = [OrderItemInline]
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

    @admin.display(description='Сумма, ₸', ordering='total_price')
    def total_price_display(self, obj):
        return _kzt(obj.total_price)

    @admin.action(description='Отметить как «Оплачен»')
    def mark_as_paid(self, request, queryset):
        queryset.update(status=Order.Status.PAID)

    @admin.action(description='Отметить как «Собирается»')
    def mark_as_processing(self, request, queryset):
        queryset.update(status=Order.Status.PROCESSING)

    @admin.action(description='Отметить как «Доставлен»')
    def mark_as_delivered(self, request, queryset):
        queryset.update(status=Order.Status.DELIVERED)
