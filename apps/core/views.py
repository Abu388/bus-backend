from django.shortcuts import render

# Create your views here.
from decimal import Decimal
import hashlib
import hmac
import json
import uuid

from django.conf import settings
from django.db import connection
from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.models import Payment, ProviderEvent
from apps.tickets.services import issue_ticket_for_payment


class HealthCheckView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['System'],
        summary='Health check endpoint (verifies database connectivity)',
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
            return Response({"status": "ok", "database": "connected"}, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"status": "error", "database": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class SimulatePaymentInputSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField()
    outcome = serializers.ChoiceField(
        choices=['succeeded', 'failed'],
        default='succeeded',
    )


class DemoPaymentSimulatorView(APIView):
    """
    Allows triggering a payment webhook outcome during a live demo when ENABLE_PAYMENT_SIMULATOR=True.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SimulatePaymentInputSerializer

    @extend_schema(
        tags=['Payments - Demo Simulator'],
        summary='Simulate payment provider confirmation for live demos',
        request=SimulatePaymentInputSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        if not getattr(settings, 'ENABLE_PAYMENT_SIMULATOR', True):
            return Response(
                {"detail": "Payment simulator is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SimulatePaymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment_id = serializer.validated_data['payment_id']
        outcome = serializer.validated_data['outcome']

        payment = Payment.objects.filter(id=payment_id).first()
        if not payment:
            return Response({"detail": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

        if payment.status != Payment.Status.PENDING:
            return Response(
                {"detail": f"Payment is already {payment.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        event_id = f"SIM-{uuid.uuid4().hex[:10].upper()}"
        payload = {
            "event_id": event_id,
            "provider_ref": payment.provider_ref,
            "status": outcome,
            "amount": str(payment.amount),
        }

        ProviderEvent.objects.create(
            provider=payment.provider,
            event_id=event_id,
            payload=payload,
            signature_valid=True,
            received_at=now,
            processed_at=now,
            payment=payment,
        )

        if outcome == 'succeeded':
            payment.status = Payment.Status.SUCCEEDED
            payment.paid_at = now
            payment.save(update_fields=['status', 'paid_at', 'updated_at'])
            ticket = issue_ticket_for_payment(payment)
            return Response(
                {
                    "status": "succeeded",
                    "payment_id": payment.id,
                    "ticket_id": ticket.id,
                    "ticket_code": ticket.code,
                },
                status=status.HTTP_200_OK,
            )
        else:
            payment.status = Payment.Status.FAILED
            payment.failure_reason = "Simulated failure."
            payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return Response(
                {"status": "failed", "payment_id": payment.id},
                status=status.HTTP_200_OK,
            )