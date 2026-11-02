from urllib.parse import quote
from rest_framework import serializers
from apps.trips.models import TripQRSession, Trip


class TripSerializer(serializers.ModelSerializer):

    class Meta:
        model = Trip
        fields = [
            'id', 'route', 'casher', 'physical_bus', 'started_at', #from this we just only accept form the casher only direction and physical_bus
            'previous_trip', 'ended_at', 'number', 'direction',
            'created_at', 'updated_at', 'status'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'route', 'casher',
            'number', 'status', 'started_at', 'ended_at', 'previous_trip'
        ]
        extra_kwargs = {'direction': {'required': False}}


class TripQRSessionSerializer(serializers.ModelSerializer):

    qr_image_url = serializers.SerializerMethodField()

    class Meta:

        model = TripQRSession
        fields = [
            'id', 'trip', 'token', 'qr_image_url',
            'expires_at', 'status', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'trip', 'token', 'qr_image_url',
            'expires_at', 'status', 'created_at', 'updated_at'
        ]

    def get_qr_image_url(self, obj) -> str:
        
        encoded_token = quote(obj.token)
        return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_token}"


class TripQRScanSerializer(serializers.Serializer):

    token = serializers.CharField()