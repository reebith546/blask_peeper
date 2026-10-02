from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html

from audit.admin_mixins import AuditModelAdmin

from .models import Category, Product, ProductImage


class OnSaleFilter(admin.SimpleListFilter):
    title = 'скидка'
    parameter_name = 'on_sale'

    def lookups(self, request, model_admin):
        return (('yes', 'Со скидкой'), ('no', 'Без скидки'))

    def queryset(self, request, queryset):
        on_sale = Q(discount_price__isnull=False) & Q(discount_price__lt=F('price'))
        if self.value() == 'yes':
            return queryset.filter(on_sale)
        if self.value() == 'no':
            return queryset.exclude(on_sale)
        return queryset


@admin.register(Category)
class CategoryAdmin(AuditModelAdmin, admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'description', 'order', 'is_active', 'show_on_homepage')
    list_editable = ('order', 'is_active', 'show_on_homepage')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'description')
    fields = ('name', 'slug', 'description', 'image', 'order', 'is_active', 'show_on_homepage')

    @admin.display(description='Фото')
    def thumbnail(self, obj):
        if not obj.image:
            return '—'
        return format_html('<img src="{}" style="width:48px;height:48px;object-fit:cover;">', obj.image.url)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Product)
class ProductAdmin(AuditModelAdmin, admin.ModelAdmin):
    list_display = (
        'thumbnail', 'name', 'category', 'price', 'discount_price', 'discount_badge',
        'in_stock', 'is_popular', 'popular_order', 'is_active',
    )
    list_editable = (
        'price', 'discount_price', 'in_stock', 'is_popular', 'popular_order', 'is_active',
    )
    list_filter = ('category', 'extra_categories', OnSaleFilter, 'is_popular', 'is_active')
    search_fields = ('name', 'composition')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('extra_categories',)
    inlines = [ProductImageInline]
    change_list_template = 'admin/catalog/product/change_list.html'

    def get_urls(self):
        custom = [
            path(
                'popular-order/',
                self.admin_site.admin_view(self.popular_order_view),
                name='catalog_product_popular_order',
            ),
        ]
        return custom + super().get_urls()

    def popular_order_view(self, request):
        """Перетаскиванием расставляем порядок «Популярных сборок» на главной."""
        if not self.has_change_permission(request):
            raise PermissionDenied
        products = (
            Product.objects.filter(is_popular=True, is_active=True)
            .order_by(F('popular_order').asc(nulls_last=True), '-created_at')
        )
        if request.method == 'POST':
            valid_ids = {str(pk) for pk in products.values_list('pk', flat=True)}
            ids = [i for i in request.POST.getlist('order') if i in valid_ids]
            with transaction.atomic():
                for position, pk in enumerate(ids, start=1):
                    Product.objects.filter(pk=pk).update(popular_order=position)
            messages.success(request, 'Порядок «Популярных сборок» сохранён.')
            return redirect('admin:catalog_product_popular_order')
        context = {
            **self.admin_site.each_context(request),
            'title': 'Порядок популярных сборок',
            'opts': self.model._meta,
            'products': products,
        }
        return render(request, 'admin/catalog/product/popular_order.html', context)

    @admin.display(description='Фото')
    def thumbnail(self, obj):
        if not obj.image:
            return '—'
        return format_html('<img src="{}" style="width:48px;height:48px;object-fit:cover;">', obj.image.url)

    @admin.display(description='Скидка')
    def discount_badge(self, obj):
        if not obj.is_on_sale:
            return '—'
        return format_html(
            '<span style="background:#1A1817;color:#fff;padding:2px 8px;'
            'border-radius:4px;font-weight:600;">-{}%</span>',
            obj.discount_percent,
        )
