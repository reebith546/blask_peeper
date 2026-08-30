from decimal import Decimal
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings

from .models import DeliveryZone, ShopLocation
from .services import haversine_km, quote_delivery, resolve_delivery_zone


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


class ZoneBoundaryTests(TestCase):
    """Границы колец: [from, to) — from включительно, to исключительно."""

    def setUp(self):
        ShopLocation.objects.create(
            name='Магазин', latitude=Decimal('43.238949'), longitude=Decimal('76.889709'),
        )
        self.inner = DeliveryZone.objects.create(name='0–5', radius_from_km=0, radius_to_km=5, price=1000)
        self.outer = DeliveryZone.objects.create(name='5–15', radius_from_km=5, radius_to_km=15, price=2000)

    def _zone_at(self, km):
        with patch('delivery.services.haversine_km', return_value=km):
            zone, _ = resolve_delivery_zone(Decimal('0'), Decimal('0'))
        return zone

    def test_zero_distance_is_inner(self):
        self.assertEqual(self._zone_at(0), self.inner)

    def test_just_below_boundary_is_inner(self):
        self.assertEqual(self._zone_at(4.999), self.inner)

    def test_exact_boundary_belongs_to_outer(self):
        self.assertEqual(self._zone_at(5.0), self.outer)

    def test_upper_edge_is_out_of_zone(self):
        self.assertIsNone(self._zone_at(15.0))

    def test_gap_between_zones_is_out_of_zone(self):
        self.outer.radius_from_km = 6
        self.outer.save()
        self.assertIsNone(self._zone_at(5.5))

    def test_overlapping_zones_pick_the_one_starting_earlier(self):
        overlap = DeliveryZone.objects.create(name='3–8', radius_from_km=3, radius_to_km=8, price=1500)
        self.assertEqual(self._zone_at(4), self.inner)   # 0–5 начинается раньше 3–8
        self.assertEqual(self._zone_at(6), overlap)      # из 3–8 и 5–15 раньше начинается 3–8


class QuoteDeliveryTests(TestCase):
    """quote_delivery — единственный авторитетный расчёт цены доставки."""

    ADDR = 'г. Алматы, ул. Абая, 10'

    def setUp(self):
        ShopLocation.objects.create(
            name='Магазин', latitude=Decimal('43.238949'), longitude=Decimal('76.889709'),
        )
        self.zone = DeliveryZone.objects.create(
            name='Центр', radius_from_km=0, radius_to_km=5, price=Decimal('1500'),
        )

    @override_settings(YANDEX_GEOCODER_API_KEY='')
    def test_maps_off(self):
        zone, price, dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'maps_off')
        self.assertIsNone(zone)
        self.assertEqual(price, Decimal('0'))

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    def test_no_shop(self):
        ShopLocation.objects.all().delete()
        with patch('delivery.services.geocode_address') as g:
            zone, price, dist, state = quote_delivery(self.ADDR)
        g.assert_not_called()
        self.assertEqual(state, 'no_shop')
        self.assertEqual(price, Decimal('0'))

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    def test_empty_address(self):
        zone, price, dist, state = quote_delivery('   ')
        self.assertEqual(state, 'geocode_failed')
        self.assertEqual(price, Decimal('0'))

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    @patch('delivery.services.geocode_address', return_value=(None, None))
    def test_address_not_found(self, _g):
        zone, price, dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'geocode_failed')
        self.assertEqual(price, Decimal('0'))

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    @patch('delivery.services.geocode_address', side_effect=requests.RequestException('boom'))
    def test_geocoder_unavailable(self, _g):
        zone, price, dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'geocode_failed')
        self.assertEqual(price, Decimal('0'))

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    @patch('delivery.services.geocode_address', return_value=(44.5, 76.8))  # ~140 км от магазина
    def test_out_of_zone(self, _g):
        zone, price, dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'out_of_zone')
        self.assertIsNone(zone)
        self.assertEqual(price, Decimal('0'))
        self.assertGreater(dist, 15)

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    @patch('delivery.services.geocode_address', return_value=(43.240000, 76.891000))
    def test_ok_returns_exact_zone_price(self, _g):
        zone, price, dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'ok')
        self.assertEqual(zone, self.zone)
        self.assertEqual(price, Decimal('1500'))
        self.assertIsInstance(price, Decimal)

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    @patch('delivery.services.geocode_address', return_value=(44.5, 76.8))  # ~140 км
    def test_on_request_zone(self, _g):
        far = DeliveryZone.objects.create(
            name='За городом', radius_from_km=100, radius_to_km=5000,
            price=None, price_on_request=True,
        )
        zone, price, dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'on_request')
        self.assertEqual(zone, far)          # зона известна — нужна менеджеру
        self.assertEqual(price, Decimal('0'))

    @override_settings(YANDEX_GEOCODER_API_KEY='key')
    @patch('delivery.services.geocode_address', return_value=(43.240000, 76.891000))
    def test_on_request_flag_wins_over_price_even_if_price_set(self, _g):
        self.zone.price_on_request = True
        self.zone.save()
        _zone, price, _dist, state = quote_delivery(self.ADDR)
        self.assertEqual(state, 'on_request')
        self.assertEqual(price, Decimal('0'))


class DeliveryZoneValidationTests(TestCase):
    def test_price_required_unless_on_request(self):
        from django.core.exceptions import ValidationError

        zone = DeliveryZone(name='Без цены', radius_from_km=0, radius_to_km=5, price=None)
        with self.assertRaises(ValidationError):
            zone.full_clean()

    def test_on_request_zone_valid_without_price(self):
        zone = DeliveryZone(
            name='За городом', radius_from_km=100, radius_to_km=5000,
            price=None, price_on_request=True,
        )
        zone.full_clean()  # не должно бросать
