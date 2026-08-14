from django.contrib import admin
from django.utils.html import format_html

from .models import HomepageBlock, NewsletterSubscriber


@admin.register(HomepageBlock)
class HomepageBlockAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'block_type', 'title', 'order', 'is_active')
    list_editable = ('order', 'is_active')
    list_filter = ('block_type', 'is_active')

    @admin.display(description='Фото')
    def thumbnail(self, obj):
        if not obj.image:
            return '—'
        return format_html('<img src="{}" style="width:48px;height:48px;object-fit:cover;">', obj.image.url)


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'created_at')
    search_fields = ('email',)
    readonly_fields = ('created_at',)
