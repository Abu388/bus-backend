from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.fares.models import Fare
from apps.fleet.models import PhysicalBus
from apps.routes.models import Route
from apps.tracking.models import BusLiveState
from apps.trips.models import Trip, TripQRSession

User = get_user_model()


class FleetTripSyncTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='admin1',
            email='admin1@example.com',
            phone='+251910000000',
            full_name='Admin User',
            role='admin',
        )
        self.casher1 = User.objects.create_user(
            username='casher1',
            email='casher1@example.com',
            phone='+251911111111',
            full_name='Casher One',
            role='casher',
        )
        self.casher2 = User.objects.create_user(
            username='casher2',
            email='casher2@example.com',
            phone='+251922222222',
            full_name='Casher Two',
            role='casher',
        )
        self.route = Route.objects.create(
            public_bus_number=50,
            name='Route 50',
            status=Route.RouteStatus.ACTIVE,
        )
        self.fare = Fare.objects.create(
            route=self.route,
            amount=Decimal('10.00'),
            effective_from=timezone.now() - timedelta(days=1),
            configured_by=self.admin,
        )
        self.client.force_authenticate(user=self.admin)

    def test_auto_start_trip_on_bus_creation_with_casher(self):
        response = self.client.post(
            '/api/buses/',
            {
                'targa': '3-C10001',
                'route': str(self.route.id),
                'casher': str(self.casher1.id),
                'status': 'active',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        bus_id = response.json()['id']

        # Verify active trip was created automatically
        trip = Trip.objects.filter(physical_bus_id=bus_id, status=Trip.Status.ACTIVE).first()
        self.assertIsNotNone(trip)
        self.assertEqual(trip.casher, self.casher1)
        self.assertEqual(trip.route, self.route)
        self.assertEqual(trip.direction, Trip.DirectionDisplay.OUTBOUND)
        self.assertEqual(trip.number, 501)

        # Verify QR session was created
        qr = TripQRSession.objects.filter(trip=trip, status=TripQRSession.Status.ACTIVE).first()
        self.assertIsNotNone(qr)

        # Verify audit log recorded
        audit = AuditLog.objects.filter(action=AuditLog.Action.TRIP_STARTED, entity_id=str(trip.id)).first()
        self.assertIsNotNone(audit)

    def test_auto_start_trip_on_bus_update_casher_assignment(self):
        bus = PhysicalBus.objects.create(
            targa='3-C10002',
            route=self.route,
            casher=None,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        self.assertFalse(Trip.objects.filter(physical_bus=bus).exists())

        response = self.client.patch(
            f'/api/buses/{bus.id}/',
            {'casher': str(self.casher1.id)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        trip = Trip.objects.filter(physical_bus=bus, status=Trip.Status.ACTIVE).first()
        self.assertIsNotNone(trip)
        self.assertEqual(trip.casher, self.casher1)

    def test_auto_stop_trip_on_casher_unassign(self):
        bus = PhysicalBus.objects.create(
            targa='3-C10003',
            route=self.route,
            casher=self.casher1,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        trip = Trip.objects.create(
            route=self.route,
            physical_bus=bus,
            casher=self.casher1,
            number=502,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        qr = TripQRSession.objects.create(
            trip=trip,
            token='tok-123456',
            status=TripQRSession.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(hours=12),
        )
        live = BusLiveState.objects.create(
            bus=bus,
            trip=trip,
            latitude=9.0,
            longitude=38.0,
            recorded_at=timezone.now(),
            received_at=timezone.now(),
        )

        # Unassign casher
        response = self.client.patch(
            f'/api/buses/{bus.id}/',
            {'casher': None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify trip ended
        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.ENDED)
        self.assertIsNotNone(trip.ended_at)

        # Verify QR revoked
        qr.refresh_from_db()
        self.assertEqual(qr.status, TripQRSession.Status.REVOKED)

        # Verify live state trip cleared
        live.refresh_from_db()
        self.assertIsNone(live.trip)

        # Verify audit log
        audit = AuditLog.objects.filter(action=AuditLog.Action.TRIP_ENDED, entity_id=str(trip.id)).first()
        self.assertIsNotNone(audit)

    def test_auto_stop_and_start_when_casher_replaced(self):
        bus = PhysicalBus.objects.create(
            targa='3-C10004',
            route=self.route,
            casher=self.casher1,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        trip1 = Trip.objects.create(
            route=self.route,
            physical_bus=bus,
            casher=self.casher1,
            number=503,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        qr1 = TripQRSession.objects.create(
            trip=trip1,
            token='tok-replace-1',
            status=TripQRSession.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(hours=12),
        )

        # Replace casher1 with casher2
        response = self.client.patch(
            f'/api/buses/{bus.id}/',
            {'casher': str(self.casher2.id)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Trip1 ended
        trip1.refresh_from_db()
        self.assertEqual(trip1.status, Trip.Status.ENDED)
        qr1.refresh_from_db()
        self.assertEqual(qr1.status, TripQRSession.Status.REVOKED)

        # Trip2 started for casher2 with flipped direction (INBOUND)
        trip2 = Trip.objects.filter(physical_bus=bus, status=Trip.Status.ACTIVE).first()
        self.assertIsNotNone(trip2)
        self.assertEqual(trip2.casher, self.casher2)
        self.assertEqual(trip2.direction, Trip.DirectionDisplay.INBOUND)

    def test_auto_stop_trip_when_bus_status_changed_to_maintenance(self):
        bus = PhysicalBus.objects.create(
            targa='3-C10005',
            route=self.route,
            casher=self.casher1,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        trip = Trip.objects.create(
            route=self.route,
            physical_bus=bus,
            casher=self.casher1,
            number=504,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )

        response = self.client.patch(
            f'/api/buses/{bus.id}/',
            {'status': 'maintenance'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.ENDED)

    def test_auto_stop_trip_on_maintenance_event_create(self):
        bus = PhysicalBus.objects.create(
            targa='3-C10006',
            route=self.route,
            casher=self.casher1,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        trip = Trip.objects.create(
            route=self.route,
            physical_bus=bus,
            casher=self.casher1,
            number=505,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        qr = TripQRSession.objects.create(
            trip=trip,
            token='tok-maint-event',
            status=TripQRSession.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(hours=12),
        )

        # Post maintenance event
        response = self.client.post(
            '/api/maintenance/',
            {
                'bus': str(bus.id),
                'reason': 'Flat tire replacement',
                'started_at': timezone.now().isoformat(),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        bus.refresh_from_db()
        self.assertEqual(bus.status, PhysicalBus.BusStatus.MAINTENANCE)

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.ENDED)

        qr.refresh_from_db()
        self.assertEqual(qr.status, TripQRSession.Status.REVOKED)

    def test_auto_start_cleans_orphaned_trip_of_casher(self):
        bus_old = PhysicalBus.objects.create(
            targa='3-C10007',
            route=self.route,
            casher=None,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        orphaned_trip = Trip.objects.create(
            route=self.route,
            physical_bus=bus_old,
            casher=self.casher1,
            number=506,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )

        bus_new = PhysicalBus.objects.create(
            targa='3-C10008',
            route=self.route,
            casher=None,
            status=PhysicalBus.BusStatus.ACTIVE,
        )

        # Assign casher1 to bus_new
        response = self.client.patch(
            f'/api/buses/{bus_new.id}/',
            {'casher': str(self.casher1.id)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        orphaned_trip.refresh_from_db()
        self.assertEqual(orphaned_trip.status, Trip.Status.ENDED)

        new_trip = Trip.objects.filter(physical_bus=bus_new, status=Trip.Status.ACTIVE).first()
        self.assertIsNotNone(new_trip)
        self.assertEqual(new_trip.casher, self.casher1)

    def test_no_trip_started_if_no_active_fare(self):
        route_no_fare = Route.objects.create(
            public_bus_number=99,
            name='No Fare Route',
            status=Route.RouteStatus.ACTIVE,
        )
        bus = PhysicalBus.objects.create(
            targa='3-C10009',
            route=route_no_fare,
            casher=None,
            status=PhysicalBus.BusStatus.ACTIVE,
        )

        response = self.client.patch(
            f'/api/buses/{bus.id}/',
            {'casher': str(self.casher1.id)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # No trip should be started
        self.assertFalse(Trip.objects.filter(physical_bus=bus).exists())
