from django.contrib import admin
from django.utils.html import format_html

from .models import Category, Product, ProductImage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'order', 'is_active')
    list_editable = ('order', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)

    @admin.display(description='Фото')
    def thumbnail(self, obj):
        if not obj.image:
            return '—'
        return format_html('<img src="{}" style="width:48px;height:48px;object-fit:cover;">', obj.image.url)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'category', 'price', 'stock', 'is_popular', 'is_active')
    list_editable = ('price', 'stock', 'is_popular', 'is_active')
    list_filter = ('category', 'is_popular', 'is_active')
    search_fields = ('name', 'composition')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline]

    @admin.display(description='Фото')
    def thumbnail(self, obj):
        if not obj.image:
            return '—'
        return format_html('<img src="{}" style="width:48px;height:48px;object-fit:cover;">', obj.image.url)
