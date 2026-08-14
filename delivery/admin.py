from django.contrib import admin

from .models import DeliveryZone


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'order', 'is_active')
    list_editable = ('price', 'order', 'is_active')
    search_fields = ('name',)
