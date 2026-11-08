from rest_framework import serializers
from apps.tickets.models import Ticket, TicketScan


class TicketSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    amount = serializers.DecimalField(
        source='payment.amount', max_digits=10, decimal_places=2, read_only=True
    )
    route_name = serializers.CharField(source='trip.route.name', read_only=True)
    public_bus_number = serializers.IntegerField(
        source='trip.route.public_bus_number', read_only=True
    )
    validated_at = serializers.SerializerMethodField()
    used_at = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            'id',
            'payment',
            'passenger',
            'trip',
            'code',
            'issued_at',
            'expires_at',
            'status',
            'amount',
            'route_name',
            'public_bus_number',
            'validated_at',
            'used_at',
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