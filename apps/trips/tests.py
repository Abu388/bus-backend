from datetime import timedelta
from decimal import Decimal
import secrets

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.fares.models import Fare
from apps.fleet.models import PhysicalBus
from apps.routes.models import Route
from apps.trips.models import Trip, TripQRSession

User = get_user_model()


class TripQRScanTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.casher = User.objects.create_user(
            username='casher_qr',
            email='casher_qr@example.com',
            phone='+251911999888',
            full_name='QR Casher',
            role='casher',
        )
        self.route = Route.objects.create(
            public_bus_number=25,
            name='Route 25 Stadium - Bole',
            status=Route.RouteStatus.ACTIVE,
        )
        self.fare = Fare.objects.create(
            route=self.route,
            amount=Decimal('15.00'),
            effective_from=timezone.now() - timedelta(days=1),
            configured_by=self.casher,
        )
        self.bus = PhysicalBus.objects.create(
            targa='3-A99887',
            route=self.route,
            casher=self.casher,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        self.trip = Trip.objects.create(
            route=self.route,
            physical_bus=self.bus,
            casher=self.casher,
            number=801,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        self.qr_token = secrets.token_urlsafe(32)
        self.qr_session = TripQRSession.objects.create(
            trip=self.trip,
            token=self.qr_token,
            status=TripQRSession.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(hours=6),
        )

    def test_trip_qr_scan_returns_fare_amount_and_aliases(self):
        resp = self.client.post(
            '/api/trip-qr/scan/',
            {'token': self.qr_token},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data['trip_id'], str(self.trip.id))
        self.assertEqual(data['trip_number'], 801)
        self.assertEqual(data['public_bus_number'], 25)
        self.assertEqual(data['route_name'], 'Route 25 Stadium - Bole')
        self.assertEqual(data['direction'], Trip.DirectionDisplay.OUTBOUND)
        self.assertEqual(data['current_fare'], '15.00')
        self.assertEqual(data['fare_amount'], '15.00')
        self.assertEqual(data['amount'], '15.00')
        self.assertEqual(data['unit_fare'], '15.00')
