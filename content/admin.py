from django.contrib import admin

from .models import HomepageBlock


@admin.register(HomepageBlock)
class HomepageBlockAdmin(admin.ModelAdmin):
    list_display = ('block_type', 'title', 'order', 'is_active')
    list_editable = ('order', 'is_active')
    list_filter = ('block_type', 'is_active')
