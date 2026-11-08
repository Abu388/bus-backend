from datetime import timedelta
from decimal import Decimal
import secrets

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
from apps.tickets.models import Ticket
from apps.trips.models import Trip, TripQRSession

User = get_user_model()


class InstantPaymentTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.passenger = User.objects.create_user(
            username='passenger1',
            email='passenger1@example.com',
            phone='+251912345678',
            full_name='Passenger One',
            role='passenger',
        )
        self.casher = User.objects.create_user(
            username='casher1',
            email='casher1@example.com',
            phone='+251911111111',
            full_name='Casher One',
            role='casher',
        )
        self.route = Route.objects.create(
            public_bus_number=10,
            name='Route 10',
            status=Route.RouteStatus.ACTIVE,
        )
        self.fare = Fare.objects.create(
            route=self.route,
            amount=Decimal('15.00'),
            effective_from=timezone.now() - timedelta(days=1),
            configured_by=self.casher,
        )
        self.bus = PhysicalBus.objects.create(
            targa='3-B99999',
            route=self.route,
            casher=self.casher,
            status=PhysicalBus.BusStatus.ACTIVE,
        )
        self.trip = Trip.objects.create(
            route=self.route,
            physical_bus=self.bus,
            casher=self.casher,
            number=600,
            direction=Trip.DirectionDisplay.OUTBOUND,
            status=Trip.Status.ACTIVE,
            started_at=timezone.now(),
        )
        self.qr_token = secrets.token_urlsafe(32)
        self.qr_session = TripQRSession.objects.create(
            trip=self.trip,
            token=self.qr_token,
            status=TripQRSession.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(hours=12),
        )

    def test_instant_payment_confirmation_and_ticket_creation(self):
        self.client.force_authenticate(user=self.passenger)
        idempotency_key = 'idem-test-123'

        response = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': idempotency_key},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data['status'], Payment.Status.SUCCEEDED)
        self.assertIsNotNone(data['paid_at'])
        self.assertIsNotNone(data['ticket_id'])
        self.assertIsNotNone(data['ticket'])
        self.assertEqual(data['ticket']['id'], data['ticket_id'])
        self.assertEqual(data['route_name'], 'Route 10')
        self.assertEqual(data['public_bus_number'], 10)

        # Verify Payment model in DB
        payment = Payment.objects.get(id=data['id'])
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertIsNotNone(payment.paid_at)

        # Verify Ticket model in DB
        ticket = Ticket.objects.get(payment=payment)
        self.assertEqual(str(ticket.id), data['ticket_id'])
        self.assertEqual(ticket.passenger, self.passenger)
        self.assertEqual(ticket.trip, self.trip)
        self.assertEqual(ticket.get_computed_status(), 'valid')

        # Verify AuditLog entries
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.PAYMENT_INITIATED, entity_id=str(payment.id)).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.PAYMENT_SUCCEEDED, entity_id=str(payment.id)).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.TICKET_ISSUED, entity_id=str(ticket.id)).exists()
        )

        # Verify GET /api/payments/{id}/
        detail_resp = self.client.get(f"/api/payments/{payment.id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        detail_data = detail_resp.json()
        self.assertEqual(detail_data['status'], 'succeeded')
        self.assertEqual(detail_data['ticket_id'], str(ticket.id))
        self.assertIsNotNone(detail_data['ticket'])
        self.assertEqual(detail_data['ticket']['id'], str(ticket.id))
        self.assertEqual(detail_data['route_name'], 'Route 10')
        self.assertEqual(detail_data['public_bus_number'], 10)

        # Verify GET /api/tickets/ reflects new ticket
        tickets_resp = self.client.get('/api/tickets/')
        self.assertEqual(tickets_resp.status_code, status.HTTP_200_OK)
        tickets_list = tickets_resp.json()
        self.assertEqual(len(tickets_list), 1)
        self.assertEqual(tickets_list[0]['id'], str(ticket.id))
        self.assertEqual(tickets_list[0]['code'], ticket.code)

    def test_instant_payment_via_api_payments_route(self):
        self.client.force_authenticate(user=self.passenger)
        response = self.client.post(
            '/api/payments/',
            {'token': self.qr_token, 'idempotency_key': 'idem-payments-route'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data['status'], 'succeeded')
        self.assertIsNotNone(data['ticket_id'])
        self.assertIsNotNone(data['ticket'])

    def test_idempotent_retry_returns_existing_payment_and_ticket(self):
        self.client.force_authenticate(user=self.passenger)
        idempotency_key = 'idem-retry-123'

        resp1 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': idempotency_key},
            format='json',
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        resp2 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': idempotency_key},
            format='json',
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json()['id'], resp2.json()['id'])
        self.assertEqual(resp1.json()['ticket_id'], resp2.json()['ticket_id'])
        self.assertEqual(resp2.json()['ticket']['id'], resp1.json()['ticket_id'])

    def test_self_healing_on_payment_detail_retrieval(self):
        # Create a pending payment without a ticket directly in DB
        pending_payment = Payment.objects.create(
            trip=self.trip,
            passenger=self.passenger,
            fare=self.fare,
            amount=self.fare.amount,
            status=Payment.Status.PENDING,
            provider='mock',
            provider_ref='MOCK-PENDING-1',
            idempotency_key='idem-pending-1',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.assertFalse(Ticket.objects.filter(payment=pending_payment).exists())

        # Passenger opens GET /api/payments/<id>/
        self.client.force_authenticate(user=self.passenger)
        resp = self.client.get(f"/api/payments/{pending_payment.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()

        # Should be healed to SUCCEEDED and ticket created
        self.assertEqual(data['status'], 'succeeded')
        self.assertIsNotNone(data['paid_at'])
        self.assertIsNotNone(data['ticket_id'])
        self.assertIsNotNone(data['ticket'])
        self.assertEqual(data['ticket']['id'], data['ticket_id'])

        # DB must also reflect succeeded status and created ticket
        pending_payment.refresh_from_db()
        self.assertEqual(pending_payment.status, Payment.Status.SUCCEEDED)
        self.assertTrue(Ticket.objects.filter(payment=pending_payment).exists())

    def test_rescan_same_trip_with_different_idempotency_key_self_heals(self):
        self.client.force_authenticate(user=self.passenger)
        # First scan
        resp1 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': 'key-scan-1'},
            format='json',
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        # Passenger rescans or refreshes with a different key
        resp2 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': 'key-scan-2'},
            format='json',
        )
        # Must return 200 OK with the existing payment and ticket instead of 400 Conflict!
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.json()['id'], resp1.json()['id'])
        self.assertEqual(resp2.json()['ticket_id'], resp1.json()['ticket_id'])

    def test_payments_list_endpoint_and_self_healing(self):
        # Create a pending payment
        p = Payment.objects.create(
            trip=self.trip,
            passenger=self.passenger,
            fare=self.fare,
            amount=self.fare.amount,
            status=Payment.Status.PENDING,
            provider='mock',
            provider_ref='MOCK-LIST-1',
            idempotency_key='idem-list-1',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.client.force_authenticate(user=self.passenger)
        resp = self.client.get('/api/payments/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        payments_data = resp.json()
        self.assertGreaterEqual(len(payments_data), 1)

        healed_p = next(x for x in payments_data if x['id'] == str(p.id))
        self.assertEqual(healed_p['status'], 'succeeded')
        self.assertIsNotNone(healed_p['ticket_id'])
        self.assertIsNotNone(healed_p['ticket'])
