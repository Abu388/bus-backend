from django.utils import timezone
from rest_framework import serializers
from apps.tracking.models import BusLiveState, BusLocation


class LocationPingInputSerializer(serializers.Serializer):
    """
    Validates incoming GPS pings from the casher's phone (POST /buses/{id}/location).
    Because this inherits from serializers.Serializer (not ModelSerializer),
    we declare each field directly with its exact min/max validation rules instead of using class Meta.
    """
    latitude = serializers.FloatField(min_value=-90.0, max_value=90.0)
    longitude = serializers.FloatField(min_value=-180.0, max_value=180.0)
    # 50 m/s = 180 km/h (blocks impossible GPS speed glitches)
    speed = serializers.FloatField(min_value=0.0, max_value=50.0, default=0.0)
    heading = serializers.FloatField(
        min_value=0.0, max_value=360.0, required=False, allow_null=True
    )
    accuracy = serializers.FloatField(
        min_value=0.0, required=False, allow_null=True
    )
    # The timestamp from the phone when the GPS coordinates were captured
    recorded_at = serializers.DateTimeField()


class BusLiveStateSerializer(serializers.ModelSerializer):
    """
    Formats the live bus state for map readers (Passengers and Admins).
    Adds two computed fields: age_seconds and is_stale.
    """
    # SerializerMethodField automatically looks for methods named get_<field_name> below
    age_seconds = serializers.SerializerMethodField()
    is_stale = serializers.SerializerMethodField()
    next_fermata_name = serializers.CharField(source='next_fermata.name', read_only=True)

    class Meta:
        model = BusLiveState
        fields = [
            'bus',
            'trip',
            'latitude',
            'longitude',
            'speed',
            'heading',
            'recorded_at',
            'received_at',
            'distance_along_m',
            'next_fermata',
            'next_fermata_name',
            'age_seconds',
            'is_stale',
        ]
        read_only_fields = fields

    def get_age_seconds(self, obj) -> float:
        # Calculates how many seconds have passed since the server received this ping
        age = (timezone.now() - obj.received_at).total_seconds()
        return round(max(age, 0.0), 1)

    def get_is_stale(self, obj) -> bool:
        # Flags the location as stale (True) if the last ping is older than 60 seconds
        age = (timezone.now() - obj.received_at).total_seconds()
        return age > 60.0