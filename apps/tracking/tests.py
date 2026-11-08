from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.fleet.models import PhysicalBus
from apps.routes.models import Fermata, Route
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
