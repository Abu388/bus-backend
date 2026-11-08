from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.routes.models import Fermata, Route, RouteStop


class RouteFermatasTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.route = Route.objects.create(
            public_bus_number=124,
            name='Mexico - Megenagna',
            status=Route.RouteStatus.ACTIVE,
        )
        self.f1 = Fermata.objects.create(name='Mexico', latitude=9.0105, longitude=38.7445)
        self.f2 = Fermata.objects.create(name='Legehar', latitude=9.0150, longitude=38.7520)
        self.f3 = Fermata.objects.create(name='Meskel Square', latitude=9.0110, longitude=38.7635)
        self.f4 = Fermata.objects.create(name='Megenagna', latitude=9.0205, longitude=38.8025)

        RouteStop.objects.create(route=self.route, fermata=self.f1, sequence_number=1)
        RouteStop.objects.create(route=self.route, fermata=self.f2, sequence_number=2)
        RouteStop.objects.create(route=self.route, fermata=self.f3, sequence_number=3)
        RouteStop.objects.create(route=self.route, fermata=self.f4, sequence_number=4)

    def test_routes_list_includes_ordered_fermatas(self):
        response = self.client.get('/api/routes/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        routes = response.json()
        self.assertEqual(len(routes), 1)

        route_data = routes[0]
        self.assertIn('fermatas', route_data)
        fermatas = route_data['fermatas']
        self.assertEqual(len(fermatas), 4)

        # Verify correct ordering and required fields
        names = [f['name'] for f in fermatas]
        self.assertEqual(names, ['Mexico', 'Legehar', 'Meskel Square', 'Megenagna'])

        for idx, f in enumerate(fermatas, start=1):
            self.assertEqual(f['sequence_number'], idx)
            self.assertEqual(f['order'], idx)
            self.assertIn('latitude', f)
            self.assertIn('longitude', f)

    def test_route_detail_includes_ordered_fermatas(self):
        response = self.client.get(f'/api/routes/{self.route.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertIn('fermatas', data)
        self.assertEqual(len(data['fermatas']), 4)
        self.assertEqual(data['fermatas'][0]['name'], 'Mexico')
        self.assertEqual(data['fermatas'][3]['name'], 'Megenagna')

    def test_routes_list_and_detail_expose_geometry_polyline_and_coordinates(self):
        # GET /api/routes/
        response = self.client.get('/api/routes/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        route_data = response.json()[0]

        self.assertIn('geometry', route_data)
        self.assertIn('polyline', route_data)
        self.assertIn('coordinates', route_data)
        self.assertIsInstance(route_data['geometry'], list)
        self.assertTrue(len(route_data['geometry']) >= 4)
        self.assertEqual(route_data['geometry'], route_data['polyline'])
        self.assertEqual(route_data['geometry'], route_data['coordinates'])

        # GET /api/routes/<id>/
        detail_resp = self.client.get(f'/api/routes/{self.route.id}/')
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        detail_data = detail_resp.json()
        self.assertIn('geometry', detail_data)
        self.assertIn('polyline', detail_data)
        self.assertIn('coordinates', detail_data)
        self.assertTrue(len(detail_data['geometry']) >= 4)

