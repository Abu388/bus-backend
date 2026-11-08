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
from apps.payments.models import Payment
from apps.routes.models import Route
from apps.tickets.models import Ticket, TicketScan
from apps.tickets.serializers import TicketSerializer
from apps.tickets.services import generate_rotating_ticket_token
from apps.trips.models import Trip

User = get_user_model()


class TicketValidationStatusTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.passenger = User.objects.create_user(
            username='passenger_test',
            email='passenger_test@example.com',
            phone='+251910001111',
            full_name='Test Passenger',
            role='passenger',
        )
        self.casher = User.objects.create_user(
            username='casher_test',
            email='casher_test@example.com',
            phone='+251920002222',
            full_name='Test Casher',
            role='casher',
        )
        self.route = Route.objects.create(
            public_bus_number=45,
            name='Autobus Tera - Megenagna',
            status=Route.RouteStatus.ACTIVE,
        )
        self.fare = Fare.objects.create(
            route=self.route,
            amount=Decimal('12.00'),
            effective_from=timezone.now() - timedelta(days=1),
            configured_by=self.casher,
        )
        self.bus = PhysicalBus.objects.create(
            targa='3-A55555',
            route=self.route,
            casher=self.casher,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        self.trip = Trip.objects.create(
            route=self.route,
            physical_bus=self.bus,
            casher=self.casher,
            number=701,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        self.payment = Payment.objects.create(
            trip=self.trip,
            passenger=self.passenger,
            fare=self.fare,
            amount=Decimal('12.00'),
            status=Payment.Status.SUCCEEDED,
            paid_at=timezone.now(),
            provider='mock',
            provider_ref='MOCK-TEST-1234',
            idempotency_key='test-ticket-key',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.ticket = Ticket.objects.create(
            payment=self.payment,
            passenger=self.passenger,
            trip=self.trip,
            code='T100',
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=3),
        )

    def test_unscanned_ticket_status_is_valid(self):
        serializer = TicketSerializer(self.ticket)
        data = serializer.data
        self.assertEqual(data['status'], 'valid')
        self.assertIsNone(data['validated_at'])
        self.assertIsNone(data['used_at'])

    def test_scanned_ticket_returns_used_status_and_timestamps(self):
        # 1. Generate rotating QR token for passenger
        code_data = generate_rotating_ticket_token(self.ticket)
        rotating_token = code_data['token']

        # 2. Casher validates the ticket
        self.client.force_authenticate(user=self.casher)
        val_resp = self.client.post(
            '/api/tickets/validate/',
            {'token': rotating_token},
            format='json',
        )
        self.assertEqual(val_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(val_resp.json()['result'], TicketScan.Result.VALID)

        # 3. Check TicketSerializer returns "used" and exposes validated_at / used_at
        self.ticket.refresh_from_db()
        serializer = TicketSerializer(self.ticket)
        data = serializer.data

        self.assertEqual(data['status'], 'used')
        self.assertIsNotNone(data['validated_at'])
        self.assertIsNotNone(data['used_at'])
        self.assertEqual(data['validated_at'], data['used_at'])

        # 4. Check GET /api/tickets/ (Passenger list) returns status "used"
        self.client.force_authenticate(user=self.passenger)
        list_resp = self.client.get('/api/tickets/')
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        tickets = list_resp.json()
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]['status'], 'used')
        self.assertIsNotNone(tickets[0]['validated_at'])
        self.assertIsNotNone(tickets[0]['used_at'])

        # 5. Check GET /api/tickets/{id}/ (Passenger detail) returns status "used"
        detail_resp = self.client.get(f'/api/tickets/{self.ticket.id}/')
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.json()['status'], 'used')

        # 6. Check GET /api/tickets/{id}/code/ returns status "used" and token None
        code_resp = self.client.get(f'/api/tickets/{self.ticket.id}/code/')
        self.assertEqual(code_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(code_resp.json()['status'], 'used')
        self.assertIsNone(code_resp.json()['token'])
        self.assertIsNone(code_resp.json()['rotating_token'])

        # 7. Check AuditLog recorded TICKET_VALIDATED
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.TICKET_VALIDATED, entity_id=str(self.ticket.id)).exists()
        )

        # 8. Check ticketscan_set alias works without AttributeError
        self.assertEqual(self.ticket.ticketscan_set.count(), 1)

    def test_second_validation_attempt_rejected_as_already_used(self):
        code_data = generate_rotating_ticket_token(self.ticket)
        rotating_token = code_data['token']

        self.client.force_authenticate(user=self.casher)
        # First scan -> Valid
        resp1 = self.client.post(
            '/api/tickets/validate/',
            {'token': rotating_token},
            format='json',
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json()['result'], 'valid')

        # Second scan -> Rejected as already used
        resp2 = self.client.post(
            '/api/tickets/validate/',
            {'token': rotating_token},
            format='json',
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.json()['result'], 'invalid')
        self.assertIn('already been used', resp2.json()['detail'])
