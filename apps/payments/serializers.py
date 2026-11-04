from rest_framework import serializers
from apps.payments.models import Payment


class PaymentInitiateSerializer(serializers.Serializer):
    """
    Validates input for POST /payments/initiate.
    The passenger sends the scanned QR token.
    Idempotency-Key is normally sent in the HTTP header, or optionally in the body for easy testing.
    """
    token = serializers.CharField(max_length=128)
    idempotency_key = serializers.CharField(
        max_length=128, required=False, allow_blank=False
    )


class PaymentSerializer(serializers.ModelSerializer):
    """
    Full payment status response for GET /payments/{id} (Passenger owner or Admin).
    All fields are read-only because the backend and webhook control the lifecycle.
    """
    ticket_id = serializers.UUIDField(source='ticket.id', read_only=True, allow_null=True)
    class Meta:
        model = Payment
        fields = [
            'id',
            'trip',
            'passenger',
            'fare',
            'amount',
            'status',
            'provider',
            'provider_ref',
            'idempotency_key',
            'created_at',
            'expires_at',
            'paid_at',
            'failure_reason',
            'ticket_id',
        ]
        read_only_fields = fields


class CasherTripPaymentSerializer(serializers.ModelSerializer):
    """
    Masked payment summary for GET /trips/{id}/payments (Casher dashboard).
    Shows only succeeded payment details and masks the passenger's identity.
    """
    passenger_masked = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id',
            'amount',
            'paid_at',
            'passenger_masked',
            'provider_ref',
        ]
        read_only_fields = fields

    def get_passenger_masked(self, obj) -> str:
        # Grabs the passenger's name, phone, or username and masks all but the last 3-4 chars
        raw_identifier = (
            getattr(obj.passenger, 'full_name', None)
            or getattr(obj.passenger, 'phone_number', None)
            or getattr(obj.passenger, 'username', None)
            or str(obj.passenger.id)
        )
        raw_str = str(raw_identifier).strip()
        if len(raw_str) <= 3:
            return f"{raw_str[0]}***"
        return f"{raw_str[:2]}***{raw_str[-2:]}"