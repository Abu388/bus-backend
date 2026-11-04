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
        ]
        read_only_fields = fields

    def get_status(self, obj) -> str:
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