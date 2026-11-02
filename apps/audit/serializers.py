from rest_framework import serializers

from apps.audit.models import AuditLog
from apps.fares.models import Fare
from apps.trips.models import Trip


class AuditLogSerializer(serializers.ModelSerializer):
    actor_id = serializers.UUIDField(source='actor.id', read_only=True, allow_null=True)
    actor_role = serializers.CharField(source='actor.role', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            'id',
            'actor_id',
            'actor_role',
            'actor_kind',
            'action',
            'entity_type',
            'entity_id',
            'metadata',
            'created_at',
        ]
        read_only_fields = fields


class BusTripReportSerializer(serializers.ModelSerializer):
    """
    Formats the 'Trip #501, 23 passengers, 575 ETB' view with database-annotated fields.
    """
    route_name = serializers.CharField(source='route.name', read_only=True)
    public_bus_number = serializers.IntegerField(
        source='route.public_bus_number', read_only=True
    )
    bus_targa = serializers.CharField(source='physical_bus.targa', read_only=True)
    passenger_count = serializers.IntegerField(read_only=True)
    revenue = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    summary_label = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = [
            'id',
            'number',
            'physical_bus',
            'bus_targa',
            'route',
            'route_name',
            'public_bus_number',
            'direction',
            'status',
            'started_at',
            'ended_at',
            'passenger_count',
            'revenue',
            'summary_label',
        ]
        read_only_fields = fields

    def get_summary_label(self, obj) -> str:
        count = getattr(obj, 'passenger_count', 0) or 0
        rev = getattr(obj, 'revenue', 0) or 0
        return f"Trip #{obj.number}, {count} passengers, {rev} ETB"


class FareHistorySerializer(serializers.ModelSerializer):
    route_name = serializers.CharField(source='route.name', read_only=True)
    public_bus_number = serializers.IntegerField(
        source='route.public_bus_number', read_only=True
    )
    configured_by_id = serializers.UUIDField(
        source='configured_by.id', read_only=True, allow_null=True
    )

    class Meta:
        model = Fare
        fields = [
            'id',
            'route',
            'route_name',
            'public_bus_number',
            'amount',
            'effective_from',
            'effective_to',
            'configured_by_id',
            'created_at',
        ]
        read_only_fields = fields

class MaintenanceStartInputSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)