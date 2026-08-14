from decimal import Decimal

from django.test import TestCase

from .models import DeliveryZone, ShopLocation
from .services import haversine_km, resolve_delivery_zone


class HaversineTests(TestCase):
    def test_same_point_is_zero_distance(self):
        self.assertAlmostEqual(haversine_km(43.0, 76.0, 43.0, 76.0), 0.0, places=6)

    def test_known_distance_is_approximately_correct(self):
        # Алматы -> Астана по прямой ~965 км.
        distance = haversine_km(43.238949, 76.889709, 51.169392, 71.449074)
        self.assertAlmostEqual(distance, 965, delta=15)


class ResolveDeliveryZoneTests(TestCase):
    def setUp(self):
        self.shop = ShopLocation.objects.create(
            name='Магазин', latitude=Decimal('43.238949'), longitude=Decimal('76.889709'),
        )
        self.near_zone = DeliveryZone.objects.create(
            name='Центр', radius_from_km=0, radius_to_km=5, price=1000,
        )
        self.far_zone = DeliveryZone.objects.create(
            name='Дальше', radius_from_km=5, radius_to_km=15, price=2000,
        )

    def test_point_close_to_shop_resolves_near_zone(self):
        zone, distance = resolve_delivery_zone(Decimal('43.240000'), Decimal('76.891000'))
        self.assertEqual(zone, self.near_zone)
        self.assertLess(distance, 5)

    def test_point_outside_all_zones_returns_none(self):
        zone, distance = resolve_delivery_zone(Decimal('44.238949'), Decimal('76.889709'))
        self.assertIsNone(zone)
        self.assertGreater(distance, 15)

    def test_no_shop_configured_returns_none(self):
        self.shop.delete()
        zone, distance = resolve_delivery_zone(Decimal('43.240000'), Decimal('76.891000'))
        self.assertIsNone(zone)
        self.assertIsNone(distance)

    def test_inactive_zone_is_ignored(self):
        self.near_zone.is_active = False
        self.near_zone.save()
        zone, distance = resolve_delivery_zone(Decimal('43.240000'), Decimal('76.891000'))
        self.assertIsNone(zone)
