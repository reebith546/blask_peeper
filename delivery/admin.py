from django.contrib import admin

from .models import DeliveryZone, ShopLocation


@admin.register(ShopLocation)
class ShopLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'latitude', 'longitude')


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'radius_from_km', 'radius_to_km', 'price', 'delivery_time_minutes', 'order', 'is_active')
    list_editable = ('radius_from_km', 'radius_to_km', 'price', 'delivery_time_minutes', 'order', 'is_active')
    search_fields = ('name',)
