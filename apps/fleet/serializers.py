from rest_framework import serializers
from apps.fleet.models import PhysicalBus , MaintenanceEvent

class PhysicalBusSerializer(serializers.ModelSerializer):
    class Meta:
        model = PhysicalBus
        fields = ['id', 'targa', 'route', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_targa(self, value):
        cleaned = value.strip().upper()
        if not cleaned:
            raise serializers.ValidationError("Targa cannot be blank.")
        return cleaned


class MaintenanceEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceEvent
        fields = ['id', 'bus', 'reason', 'description', 'started_at', 'ended_at', 'started_by', 'ended_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'started_by', 'ended_by']

    def validate(self, data):
        started_at = data.get('started_at') or getattr(self.instance, 'started_at', None)# If the instance exists (i.e., we are updating an existing MaintenanceEvent), we retrieve the existing started_at value from the instance. If the instance does not exist (i.e., we are creating a new MaintenanceEvent), we use the started_at value from the incoming data.
        ended_at = data.get('ended_at')
        if ended_at and started_at and ended_at <= started_at:
            raise serializers.ValidationError({"ended_at": "Ended at must be after started at."})
        return data