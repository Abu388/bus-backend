from rest_framework import serializers
from apps.routes.models import Fermata , Route , RouteStop ,  RouteDirection


# Fermata
class FermataSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fermata
        fields = ['id', 'name','latitude', 'longitude',  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Name cannot be blank.")
        return cleaned


# RouteStop
class RouteStopSerializer(serializers.ModelSerializer):
    fermata_detail = FermataSerializer(source='fermata', read_only=True)
    class Meta:
        model = RouteStop
        fields =  ['id', 'route','fermata', 'sequence_number',  'created_at', 'updated_at','fermata_detail']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_sequence_number(self, value):
        if value < 1:
            raise serializers.ValidationError("sequence number value must be greater than one.") 
        return value


# RouteDirection
class RouteDirectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouteDirection
        fields = ['id', 'route','direction', 'geometry','stop_distances',  'distance_m', 'duration_s', 'created_at' , 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

        
#Route
class RouteSerializer(serializers.ModelSerializer):
    stops = RouteStopSerializer(many=True, read_only=True)
    directions = RouteDirectionSerializer(many=True, read_only=True)
    class Meta:
        model = Route
        fields = ['id', 'public_bus_number', 'name', 'status', 'created_at','updated_at','stops','directions']
        read_only_fields = ['id', 'created_at', 'updated_at']
         
