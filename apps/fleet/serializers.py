from rest_framework import serializers
from apps.fleet.models import PhysicalBus, MaintenanceEvent


class PhysicalBusSerializer(serializers.ModelSerializer):
    casher_name = serializers.CharField(source='casher.full_name', read_only=True)
    route_number = serializers.IntegerField(source='route.public_bus_number', read_only=True)
    route_name = serializers.CharField(source='route.name', read_only=True)

    class Meta:
        model = PhysicalBus
        fields = [
            'id',
            'targa',
            'route',
            'route_number',
            'route_name',
            'casher',
            'casher_name',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'route_number',
            'route_name',
            'casher_name',
            'created_at',
            'updated_at',
        ]

    def validate_targa(self, value):
        cleaned = value.strip().upper()
        if not cleaned:
            raise serializers.ValidationError("Targa cannot be blank.")
        return cleaned

    def validate_casher(self, value):
        if value and value.role != 'casher':
            raise serializers.ValidationError("Only a user with the casher role can be assigned to a bus.")
        return value


class MaintenanceEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceEvent
        fields = [
            'id',
            'bus',
            'reason',
            'description',
            'started_at',
            'ended_at',
            'started_by',
            'ended_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'started_by', 'ended_by']

    def validate(self, data):
        started_at = data.get('started_at') or getattr(self.instance, 'started_at', None)
        ended_at = data.get('ended_at')
        if ended_at and started_at and ended_at <= started_at:
            raise serializers.ValidationError({"ended_at": "Ended at must be after started at."})
        return data