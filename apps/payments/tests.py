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

    def test_buy_second_ticket_on_same_trip_with_new_idempotency_key(self):
        """
        Verifies that a passenger can buy a second (additional) ticket on the same trip
        by sending a new idempotency key.
        """
        self.client.force_authenticate(user=self.passenger)
        # 1. First purchase on trip
        resp1 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': 'key-first-ticket', 'passenger_count': 1},
            format='json',
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        data1 = resp1.json()

        # 2. Second purchase on the same trip with a NEW idempotency key
        resp2 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': 'key-second-ticket', 'passenger_count': 2},
            format='json',
        )
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        data2 = resp2.json()

        # Both payments and tickets must exist and be distinct
        self.assertNotEqual(data1['id'], data2['id'])
        self.assertNotEqual(data1['ticket_id'], data2['ticket_id'])
        self.assertEqual(data1['passenger_count'], 1)
        self.assertEqual(data2['passenger_count'], 2)
        self.assertEqual(Decimal(str(data1['amount'])), Decimal('15.00'))
        self.assertEqual(Decimal(str(data2['amount'])), Decimal('30.00'))

        # Retrying the first key returns the first payment
        retry1 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': 'key-first-ticket'},
            format='json',
        )
        self.assertEqual(retry1.status_code, status.HTTP_200_OK)
        self.assertEqual(retry1.json()['id'], data1['id'])

        # Retrying the second key returns the second payment
        retry2 = self.client.post(
            '/api/payments/initiate/',
            {'token': self.qr_token, 'idempotency_key': 'key-second-ticket'},
            format='json',
        )
        self.assertEqual(retry2.status_code, status.HTTP_200_OK)
        self.assertEqual(retry2.json()['id'], data2['id'])

        # Both tickets must be linked to the passenger and trip in DB
        self.assertEqual(Ticket.objects.filter(passenger=self.passenger, trip=self.trip).count(), 2)

    def test_multi_passenger_payment_passenger_count_six(self):
        """
        Paying for passenger_count=6 on a 15.00 ETB route:
        - payment.amount == 90.00
        - payment.passenger_count == 6
        - ticket.passenger_count == 6
        - POST /api/tickets/validate/ returns passenger_count: 6
        """
        self.client.force_authenticate(user=self.passenger)
        resp = self.client.post(
            '/api/payments/initiate/',
            {
                'token': self.qr_token,
                'idempotency_key': 'idem-multi-6',
                'passenger_count': 6,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()

        # Verify payment payload & fields
        self.assertEqual(Decimal(str(data['amount'])), Decimal('90.00'))
        self.assertEqual(data['passenger_count'], 6)
        self.assertEqual(data['quantity'], 6)
        self.assertEqual(Decimal(str(data['unit_fare'])), Decimal('15.00'))

        # Verify Payment in DB
        payment = Payment.objects.get(id=data['id'])
        self.assertEqual(payment.amount, Decimal('90.00'))
        self.assertEqual(payment.passenger_count, 6)

        # Verify Ticket in DB
        ticket = Ticket.objects.get(payment=payment)
        self.assertEqual(ticket.passenger_count, 6)
        self.assertEqual(data['ticket']['passenger_count'], 6)
        self.assertEqual(data['ticket']['quantity'], 6)
        self.assertEqual(Decimal(str(data['ticket']['unit_fare'])), Decimal('15.00'))
        self.assertEqual(Decimal(str(data['ticket']['amount'])), Decimal('90.00'))

        # Verify AuditLog entries include passenger_count
        pay_init_log = AuditLog.objects.filter(
            action=AuditLog.Action.PAYMENT_INITIATED, entity_id=str(payment.id)
        ).first()
        self.assertIsNotNone(pay_init_log)
        self.assertEqual(pay_init_log.metadata.get('passenger_count'), 6)
        self.assertEqual(pay_init_log.metadata.get('unit_fare'), '15.00')

        pay_succ_log = AuditLog.objects.filter(
            action=AuditLog.Action.PAYMENT_SUCCEEDED, entity_id=str(payment.id)
        ).first()
        self.assertIsNotNone(pay_succ_log)
        self.assertEqual(pay_succ_log.metadata.get('passenger_count'), 6)
        self.assertEqual(pay_succ_log.metadata.get('unit_fare'), '15.00')

        ticket_issued_log = AuditLog.objects.filter(
            action=AuditLog.Action.TICKET_ISSUED, entity_id=str(ticket.id)
        ).first()
        self.assertIsNotNone(ticket_issued_log)
        self.assertEqual(ticket_issued_log.metadata.get('passenger_count'), 6)

        # Verify rotating code endpoint exposes passenger_count and quantity
        code_resp = self.client.get(f'/api/tickets/{ticket.id}/code/')
        self.assertEqual(code_resp.status_code, status.HTTP_200_OK)
        code_data = code_resp.json()
        self.assertEqual(code_data['passenger_count'], 6)
        self.assertEqual(code_data['quantity'], 6)
        self.assertEqual(Decimal(str(code_data['amount'])), Decimal('90.00'))

        # Casher validates the ticket via POST /api/tickets/validate/
        self.client.force_authenticate(user=self.casher)
        val_resp = self.client.post(
            '/api/tickets/validate/',
            {'token': code_data['token']},
            format='json',
        )
        self.assertEqual(val_resp.status_code, status.HTTP_200_OK)
        val_data = val_resp.json()
        self.assertEqual(val_data['result'], 'valid')
        self.assertEqual(val_data['passenger_count'], 6)
        self.assertEqual(val_data['quantity'], 6)
        self.assertEqual(Decimal(str(val_data['amount'])), Decimal('90.00'))

        # Verify TICKET_VALIDATED audit log
        ticket_val_log = AuditLog.objects.filter(
            action=AuditLog.Action.TICKET_VALIDATED, entity_id=str(ticket.id)
        ).first()
        self.assertIsNotNone(ticket_val_log)
        self.assertEqual(ticket_val_log.metadata.get('passenger_count'), 6)

    def test_quantity_alias_resolution_in_initiate_payment(self):
        """
        Verify that passing 'quantity' instead of 'passenger_count' works identically.
        """
        self.client.force_authenticate(user=self.passenger)
        resp = self.client.post(
            '/api/payments/initiate/',
            {
                'token': self.qr_token,
                'idempotency_key': 'idem-quantity-4',
                'quantity': 4,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data['passenger_count'], 4)
        self.assertEqual(data['quantity'], 4)
        self.assertEqual(Decimal(str(data['amount'])), Decimal('60.00'))

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
