from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsCasherRole
from apps.fares.models import Fare
from apps.payments.models import Payment, ProviderEvent
from apps.payments.serializers import (
    CashierTripPaymentSerializer,
    PaymentInitiateSerializer,
    PaymentSerializer,
)
from apps.trips.models import Trip, TripQRSession
from apps.tickets.services import issue_ticket_for_payment



def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verifies the HMAC-SHA256 signature sent by the payment provider in the
    'X-Webhook-Signature' HTTP header using settings.PAYMENT_WEBHOOK_SECRET.
    """
    if not signature_header:
        return False
    secret = getattr(settings, 'PAYMENT_WEBHOOK_SECRET', 'dev-webhook-secret-key')
    expected_sig = hmac.new(
        secret.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature_header.strip())


# ============================================================================
# 1. INITIATE A PAYMENT (Passenger)
#    Endpoint: POST /payments/initiate/
# ============================================================================
class InitiatePaymentView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaymentInitiateSerializer

    @extend_schema(
        tags=['Payments'],
        summary='Initiate a bus fare payment from a scanned QR token',
        description=(
            'Re-validates the trip QR token, locks in the current active route fare, '
            'and creates a pending payment. Supports idempotency via the Idempotency-Key header.'
        ),
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                description='Unique key per payment attempt so retries never create duplicate charges',
                required=False,
            ),
        ],
        request=PaymentInitiateSerializer,
        responses={200: PaymentSerializer, 201: PaymentSerializer},
    )
    def post(self, request):
        # 1. Validate the input body
        serializer = PaymentInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token'].strip()

        # 2. Extract Idempotency-Key from HTTP header (or request body fallback for easy testing)
        idempotency_key = (
            request.headers.get('Idempotency-Key')
            or serializer.validated_data.get('idempotency_key')
        )
        if not idempotency_key:
            raise ValidationError(
                {"idempotency_key": "Idempotency-Key header (or body field) is required."}
            )

        # 3. Idempotency check: if this passenger already sent this exact key, return the existing payment
        existing_payment = Payment.objects.filter(
            passenger=request.user,
            idempotency_key=idempotency_key,
        ).first()
        if existing_payment:
            return Response(
                PaymentSerializer(existing_payment).data,
                status=status.HTTP_200_OK,
            )

        # 4. Re-validate the QR token and ensure the trip is still active
        now = timezone.now()
        qr_session = (
            TripQRSession.objects.select_related('trip', 'trip__route')
            .filter(token=token)
            .first()
        )
        if not qr_session:
            raise ValidationError({"token": "Invalid QR token."})
        if qr_session.status != TripQRSession.Status.ACTIVE:
            raise ValidationError({"token": "This QR code is no longer active."})
        if qr_session.expires_at <= now:
            raise ValidationError({"token": "This QR code has expired."})
        if qr_session.trip.status != Trip.Status.ACTIVE:
            raise ValidationError({"token": "This trip has already ended."})

        trip = qr_session.trip

        # 5. Fetch the current active fare for this route
        active_fare = Fare.objects.filter(
            route=trip.route, effective_to__isnull=True
        ).first()
        if not active_fare:
            raise ValidationError({"fare": "No active fare is configured for this route."})

        # 6. Create the pending payment inside an atomic transaction
        provider_name = getattr(settings, 'PAYMENT_PROVIDER', 'mock')
        provider_ref = f"MOCK-{uuid.uuid4().hex[:12].upper()}"

        try:
            with transaction.atomic():
                # Check if passenger already has a pending or succeeded payment on this trip
                if Payment.objects.filter(
                    trip=trip,
                    passenger=request.user,
                    status__in=[Payment.Status.PENDING, Payment.Status.SUCCEEDED],
                ).exists():
                    raise ValidationError(
                        {"detail": "You already have a pending or completed payment for this trip."}
                    )

                payment = Payment.objects.create(
                    trip=trip,
                    passenger=request.user,
                    fare=active_fare,
                    amount=active_fare.amount,  # Copies exact price at payment time
                    status=Payment.Status.PENDING,
                    provider=provider_name,
                    provider_ref=provider_ref,
                    idempotency_key=idempotency_key,
                    expires_at=now + timedelta(minutes=10),
                )
        except IntegrityError:
            # If two concurrent retries hit the database at the same millisecond, return existing if idempotent
            retry_payment = Payment.objects.filter(
                passenger=request.user,
                idempotency_key=idempotency_key,
            ).first()
            if retry_payment:
                return Response(
                    PaymentSerializer(retry_payment).data,
                    status=status.HTTP_200_OK,
                )
            raise ValidationError(
                {"detail": "Conflict: You already have a pending or succeeded payment for this trip."}
            )

        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED,
        )


# ============================================================================
# 2. GET PAYMENT STATUS (Passenger Owner or Admin)
#    Endpoint: GET /payments/<uuid:pk>/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Payments'],
        summary='Get current payment status',
        description='Polled by the passenger app to check if a payment has succeeded, failed, or expired.',
    )
)
class PaymentDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaymentSerializer

    def get_object(self):
        payment = get_object_or_404(
            Payment.objects.select_related('trip', 'fare', 'passenger'),
            pk=self.kwargs['pk'],
        )
        # Only the passenger who owns the payment or an admin can view it
        if payment.passenger != self.request.user and getattr(self.request.user, 'role', None) != 'admin':
            raise PermissionDenied("You do not have permission to view this payment.")
        return payment


# ============================================================================
# 3. PROVIDER WEBHOOK HANDLER (Authenticated by HMAC Signature, NOT JWT Token)
#    Endpoint: POST /payments/webhook/<str:provider>/
# ============================================================================
class PaymentWebhookView(APIView):
    # Webhooks come from an external payment server, so they authenticate via HMAC signature
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Payments - Webhook'],
        summary='Receive payment provider webhook notification',
        description=(
            'Verifies the X-Webhook-Signature header, logs a ProviderEvent idempotently, '
            'validates the amount, and transitions the Payment from pending to succeeded/failed.'
        ),
        parameters=[
            OpenApiParameter(
                name='X-Webhook-Signature',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                description='HMAC-SHA256 hex digest of raw request body signed with PAYMENT_WEBHOOK_SECRET',
                required=True,
            ),
        ],
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT},
    )
    def post(self, request, provider: str):
        now = timezone.now()
        raw_body = request.body
        signature = request.headers.get('X-Webhook-Signature', '')

        # 1. Verify webhook HMAC signature first! Reject and change nothing if invalid.
        if not verify_webhook_signature(raw_body, signature):
            return Response(
                {"detail": "Invalid webhook signature."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        event_id = str(payload.get('event_id', '')).strip()
        provider_ref = str(payload.get('provider_ref', '')).strip()
        event_status = str(payload.get('status', '')).strip().lower()
        raw_amount = payload.get('amount')

        if not event_id or not provider_ref:
            return Response(
                {"detail": "Missing event_id or provider_ref in webhook payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # 2. Idempotency check on (provider, event_id): if already handled, return 200 immediately
            if ProviderEvent.objects.filter(provider=provider, event_id=event_id).exists():
                return Response(
                    {"status": "ignored", "detail": "Duplicate webhook event."},
                    status=status.HTTP_200_OK,
                )

            # 3. Lock the Payment row with select_for_update() so concurrent webhooks cannot race
            payment = (
                Payment.objects.select_for_update()
                .filter(provider=provider, provider_ref=provider_ref)
                .first()
            )

            # Record the incoming webhook event
            try:
                provider_event = ProviderEvent.objects.create(
                    provider=provider,
                    event_id=event_id,
                    payload=payload,
                    signature_valid=True,
                    received_at=now,
                    payment=payment,
                )
            except IntegrityError:
                # Caught if two threads sent the exact same event_id simultaneously
                return Response(
                    {"status": "ignored", "detail": "Duplicate webhook event."},
                    status=status.HTTP_200_OK,
                )

            # If no matching payment exists in our database, mark processed and return 200
            if not payment:
                provider_event.processed_at = timezone.now()
                provider_event.save(update_fields=['processed_at', 'updated_at'])
                return Response(
                    {"status": "ignored", "detail": "Payment not found."},
                    status=status.HTTP_200_OK,
                )

            # 4. Only allow transitions from 'pending' -> 'succeeded' / 'failed' / 'expired'.
            #    Ignore late webhooks arriving after a payment is already finalized.
            if payment.status != Payment.Status.PENDING:
                provider_event.processed_at = timezone.now()
                provider_event.save(update_fields=['processed_at', 'updated_at'])
                return Response(
                    {"status": "ignored", "detail": f"Payment already in {payment.status} state."},
                    status=status.HTTP_200_OK,
                )

            # 5. Process outcome
            if event_status == 'succeeded':
                # Compare amount in webhook with payment.amount — a mismatch is NEVER a success!
                try:
                    webhook_amount = Decimal(str(raw_amount))
                except (InvalidOperation, TypeError, ValueError):
                    webhook_amount = Decimal('-1')

                if webhook_amount != payment.amount:
                    payment.status = Payment.Status.FAILED
                    payment.failure_reason = (
                        f"Amount mismatch: expected {payment.amount}, received {raw_amount}."
                    )
                    payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
                else:
                    # Money moved cleanly — record success even if the trip ended while in flight
                    payment.status = Payment.Status.SUCCEEDED
                    payment.paid_at = now
                    payment.save(update_fields=['status', 'paid_at', 'updated_at'])

                    # =========================================================
                    # TODO (Next App - Tickets):
                    # Issue the Passenger Ticket here in this exact transaction!
                    # e.g., issue_ticket_for_payment(payment)
                    # =========================================================
                    issue_ticket_for_payment(payment)

            elif event_status == 'failed':
                payment.status = Payment.Status.FAILED
                payment.failure_reason = payload.get('failure_reason') or "Payment failed at provider."
                payment.save(update_fields=['status', 'failure_reason', 'updated_at'])

            elif event_status == 'expired':
                payment.status = Payment.Status.EXPIRED
                payment.failure_reason = "Payment expired before completion."
                payment.save(update_fields=['status', 'failure_reason', 'updated_at'])

            provider_event.processed_at = timezone.now()
            provider_event.save(update_fields=['processed_at', 'updated_at'])

        return Response(
            {"status": "processed", "payment_status": payment.status},
            status=status.HTTP_200_OK,
        )


# ============================================================================
# 4. CASHIER TRIP PAYMENTS DASHBOARD
#    Endpoint: GET /trips/<uuid:pk>/payments/
# ============================================================================
class TripPaymentsCashierView(APIView):
    permission_classes = [IsCasherRole]

    @extend_schema(
        tags=['Payments'],
        summary='Get succeeded payments and total collected for a trip (Cashier / Admin)',
        description=(
            'Returns all succeeded payments for a trip with masked passenger names, '
            'plus the total amount collected computed via a database aggregate.'
        ),
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, pk):
        trip = get_object_or_404(Trip, pk=pk)

        # Only that trip's cashier or an admin can inspect the trip's payments
        if getattr(request.user, 'role', None) == 'cashier' and trip.casher != request.user:
            raise PermissionDenied("You can only view payments for your own trip.")

        # Filter strictly to succeeded payments for this trip
        succeeded_qs = (
            Payment.objects.select_related('passenger')
            .filter(trip=trip, status=Payment.Status.SUCCEEDED)
            .order_by('-paid_at')
        )

        # Compute total collected with a single PostgreSQL SUM() aggregate (no Python loop!)
        aggregate_result = succeeded_qs.aggregate(total_collected=Sum('amount'))
        total_collected = aggregate_result['total_collected'] or Decimal('0.00')

        return Response(
            {
                "trip_id": trip.id,
                "trip_number": trip.number,
                "succeeded_count": succeeded_qs.count(),
                "total_collected": str(total_collected),
                "payments": CashierTripPaymentSerializer(succeeded_qs, many=True).data,
            },
            status=status.HTTP_200_OK,
        )