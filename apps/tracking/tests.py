from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.fleet.models import PhysicalBus
from apps.routes.models import Fermata, Route, RouteStop, RouteDirection
from apps.tracking.models import BusLiveState
from apps.tracking.serializers import BusLiveStateSerializer
from apps.trips.models import Trip

User = get_user_model()


class TrackingMetadataTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.casher = User.objects.create_user(
            username='casher1',
            email='casher1@example.com',
            phone='+251911111111',
            full_name='Casher One',
            role='casher',
        )
        self.route = Route.objects.create(
            public_bus_number=124,
            name='Megenagna - Tor Hailoch',
            status=Route.RouteStatus.ACTIVE,
        )
        self.bus = PhysicalBus.objects.create(
            targa='3-A12345',
            route=self.route,
            casher=self.casher,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        self.trip = Trip.objects.create(
            route=self.route,
            physical_bus=self.bus,
            casher=self.casher,
            number=501,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        self.fermata = Fermata.objects.create(
            name='Megenagna Square',
            latitude=9.0210,
            longitude=38.8010,
        )
        self.fermata2 = Fermata.objects.create(
            name='Tor Hailoch',
            latitude=9.0050,
            longitude=38.7200,
        )
        self.stop1 = RouteStop.objects.create(route=self.route, fermata=self.fermata, sequence_number=1)
        self.stop2 = RouteStop.objects.create(route=self.route, fermata=self.fermata2, sequence_number=2)
        self.direction = RouteDirection.objects.create(
            route=self.route,
            direction='outbound',
            geometry=[[38.8010, 9.0210], [38.7200, 9.0050]],
            stop_distances={str(self.fermata.id): 100.0, str(self.fermata2.id): 2500.0},
        )
        self.live_state = BusLiveState.objects.create(
            bus=self.bus,
            trip=self.trip,
            latitude=9.0200,
            longitude=38.8000,
            speed=25.0,
            heading=90.0,
            recorded_at=timezone.now(),
            received_at=timezone.now(),
            distance_along_m=150.0,
            next_fermata=self.fermata,
        )

    def test_bus_live_state_serializer_includes_route_and_bus_metadata(self):
        serializer = BusLiveStateSerializer(self.live_state)
        data = serializer.data

        self.assertEqual(data['route'], str(self.route.id))
        self.assertEqual(data['public_bus_number'], 124)
        self.assertEqual(data['route_name'], 'Megenagna - Tor Hailoch')
        self.assertEqual(data['targa'], '3-A12345')
        self.assertEqual(data['next_fermata_name'], 'Megenagna Square')

    def test_live_buses_list_view(self):
        response = self.client.get('/api/buses/live/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['public_bus_number'], 124)
        self.assertEqual(results[0]['route_name'], 'Megenagna - Tor Hailoch')
        self.assertEqual(results[0]['targa'], '3-A12345')

    def test_bus_search_by_number_view_with_valid_number(self):
        response = self.client.get('/api/buses/search/?number=124')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.json()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['public_bus_number'], 124)
        self.assertEqual(results[0]['targa'], '3-A12345')

    def test_bus_search_by_number_view_with_non_digit_guard(self):
        # Non-digit values must return empty queryset, NOT crash with integer cast error
        response = self.client.get('/api/buses/search/?number=abc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

        response = self.client.get('/api/buses/search/?number=12a')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

    def test_fallback_to_bus_route_when_trip_is_none(self):
        self.live_state.trip = None
        self.live_state.save()

        serializer = BusLiveStateSerializer(self.live_state)
        data = serializer.data

        self.assertIsNone(data['trip'])
        self.assertEqual(data['route'], str(self.route.id))
        self.assertEqual(data['public_bus_number'], 124)
        self.assertEqual(data['route_name'], 'Megenagna - Tor Hailoch')
        self.assertEqual(data['targa'], '3-A12345')

    def test_location_ping_relinks_active_trip_if_trip_was_none(self):
        self.live_state.trip = None
        self.live_state.save()
        self.client.force_authenticate(user=self.casher)

        ping_time = timezone.now()
        response = self.client.post(
            f'/api/buses/{self.bus.id}/location/',
            {
                'latitude': 9.0215,
                'longitude': 38.8015,
                'speed': 10.0,
                'recorded_at': ping_time.isoformat(),
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.live_state.refresh_from_db()
        self.assertEqual(self.live_state.trip, self.trip)

    def test_bus_eta_view_allow_any_and_response_structure(self):
        # Unauthenticated request must succeed (AllowAny)
        self.client.logout()
        response = self.client.get(f'/api/buses/{self.bus.id}/eta/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertEqual(data['bus_id'], str(self.bus.id))
        self.assertEqual(data['trip_id'], str(self.trip.id))
        self.assertEqual(data['route_id'], str(self.route.id))
        self.assertEqual(data['public_bus_number'], 124)
        self.assertEqual(data['route_name'], 'Megenagna - Tor Hailoch')
        self.assertEqual(data['direction'], 'outbound')

        # Check next_fermata structure
        self.assertIsNotNone(data['next_fermata'])
        self.assertIn('id', data['next_fermata'])
        self.assertIn('name', data['next_fermata'])
        self.assertIn('eta_seconds', data['next_fermata'])
        self.assertIn('eta_minutes', data['next_fermata'])
        self.assertIn('distance_remaining_m', data['next_fermata'])

        # Check upcoming_stops & stops
        self.assertIn('upcoming_stops', data)
        self.assertIn('stops', data)
        self.assertTrue(len(data['upcoming_stops']) > 0)
        first_stop = data['upcoming_stops'][0]
        self.assertIn('fermata_id', first_stop)
        self.assertIn('id', first_stop)
        self.assertIn('name', first_stop)
        self.assertIn('sequence_number', first_stop)
        self.assertIn('latitude', first_stop)
        self.assertIn('longitude', first_stop)
        self.assertIn('distance_m', first_stop)
        self.assertIn('eta_seconds', first_stop)
        self.assertIn('eta_minutes', first_stop)

    def test_bus_eta_view_low_speed_fallback_to_6mps(self):
        # When speed is very low (< 1.5 m/s), effective_speed must be 6.0 m/s
        self.live_state.speed = 0.5
        self.live_state.save()

        response = self.client.get(f'/api/buses/{self.bus.id}/eta/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['effective_speed'], 6.0)

    def test_bus_eta_view_relinks_active_trip_if_none(self):
        self.live_state.trip = None
        self.live_state.save()

        response = self.client.get(f'/api/buses/{self.bus.id}/eta/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['trip_id'], str(self.trip.id))

    def test_bus_eta_view_fallback_to_bus_route_when_no_active_trip(self):
        self.trip.status = Trip.Status.ENDED
        self.trip.ended_at = timezone.now()
        self.trip.save()
        self.live_state.trip = None
        self.live_state.save()

        response = self.client.get(f'/api/buses/{self.bus.id}/eta/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsNone(data['trip_id'])
        self.assertEqual(data['route_id'], str(self.route.id))
        self.assertEqual(data['public_bus_number'], 124)
        self.assertEqual(data['direction'], 'outbound')


    def test_location_get_relinks_active_trip_if_trip_was_none(self):
        self.live_state.trip = None
        self.live_state.save()
        self.client.force_authenticate(user=self.casher)

        response = self.client.get(f'/api/buses/{self.bus.id}/location/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['route'], str(self.route.id))
        self.assertEqual(data['public_bus_number'], 124)

        self.live_state.refresh_from_db()
        self.assertEqual(self.live_state.trip, self.trip)

