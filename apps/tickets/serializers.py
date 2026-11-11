from rest_framework import serializers
from apps.tickets.models import Ticket, TicketScan
from apps.tickets.services import generate_ticket_qr_token


class TicketSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    amount = serializers.DecimalField(
        source='payment.amount', max_digits=10, decimal_places=2, read_only=True
    )
    passenger_count = serializers.IntegerField(read_only=True)
    quantity = serializers.IntegerField(source='passenger_count', read_only=True)
    unit_fare = serializers.DecimalField(
        source='payment.fare.amount', max_digits=10, decimal_places=2, read_only=True
    )
    route_name = serializers.CharField(source='trip.route.name', read_only=True)
    public_bus_number = serializers.IntegerField(
        source='trip.route.public_bus_number', read_only=True
    )
    validated_at = serializers.SerializerMethodField()
    used_at = serializers.SerializerMethodField()
    qr_token = serializers.SerializerMethodField()
    token = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            'id',
            'payment',
            'passenger',
            'trip',
            'passenger_count',
            'quantity',
            'unit_fare',
            'code',
            'issued_at',
            'expires_at',
            'status',
            'amount',
            'route_name',
            'public_bus_number',
            'validated_at',
            'used_at',
            'qr_token',
            'token',
        ]
        read_only_fields = fields

    def _get_validation_scan(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'scans' in obj._prefetched_objects_cache:
            for scan in obj.scans.all():
                if scan.result == TicketScan.Result.VALID:
                    return scan
            return None
        if not hasattr(obj, '_cached_valid_scan'):
            obj._cached_valid_scan = (
                obj.scans.filter(result=TicketScan.Result.VALID)
                .order_by('scanned_at')
                .first()
            )
        return obj._cached_valid_scan

    def get_validated_at(self, obj):
        if getattr(obj, 'validated_at', None) is not None:
            return obj.validated_at
        if getattr(obj, 'used_at', None) is not None:
            return obj.used_at
        scan = self._get_validation_scan(obj)
        return scan.scanned_at if scan else None

    def get_used_at(self, obj):
        return self.get_validated_at(obj)

    def get_status(self, obj) -> str:
        if self.get_validated_at(obj) is not None:
            return 'used'
        return obj.get_computed_status()

    def get_qr_token(self, obj) -> str | None:
        if self.get_status(obj) != 'valid':
            return None
        if not hasattr(obj, '_cached_qr_token'):
            obj._cached_qr_token = generate_ticket_qr_token(obj)
        return obj._cached_qr_token

    def get_token(self, obj) -> str | None:
        return self.get_qr_token(obj)


class TicketValidateInputSerializer(serializers.Serializer):
    """
    Validates input for POST /tickets/validate.
    The casher sends the rotating signed token (or short ticket code).
    """
    token = serializers.CharField(max_length=512)


class TicketScanSerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketScan
        fields = [
            'id',
            'ticket',
            'casher',
            'trip',
            'result',
            'scanned_at',
        ]
        read_only_fields = fields