import json
import urllib.request
from django.core.cache import cache
from rest_framework import serializers
from apps.routes.models import Fermata, Route, RouteStop, RouteDirection


def fetch_osrm_geometry(points: list[tuple[float, float]]) -> list[list[float]]:
    """
    Given a list of (longitude, latitude) tuples, queries OSRM driving service
    to retrieve road-snapped polyline coordinates.
    Falls back gracefully to ordered stop coordinates if OSRM is unreachable.
    Caches results for 24 hours.
    """
    if len(points) < 2:
        return [[float(p[0]), float(p[1])] for p in points]

    coord_str = ';'.join(f"{float(p[0]):.6f},{float(p[1]):.6f}" for p in points)
    cache_key = f"osrm_geom_{hash(coord_str)}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = f"https://router.project-osrm.org/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'bus-backend/1.0'})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('code') == 'Ok' and data.get('routes'):
                coords = data['routes'][0]['geometry']['coordinates']
                cache.set(cache_key, coords, timeout=86400)
                return coords
    except Exception:
        pass

    fallback = [[float(p[0]), float(p[1])] for p in points]
    return fallback


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
    geometry = serializers.SerializerMethodField()
    polyline = serializers.SerializerMethodField()
    coordinates = serializers.SerializerMethodField()

    class Meta:
        model = RouteDirection
        fields = [
            'id',
            'route',
            'direction',
            'geometry',
            'polyline',
            'coordinates',
            'stop_distances',
            'distance_m',
            'duration_s',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_geometry(self, obj):
        if obj.geometry and isinstance(obj.geometry, list) and len(obj.geometry) >= 2:
            return obj.geometry
        # Fallback from stops for this route and direction
        stops = list(obj.route.stops.select_related('fermata').order_by('sequence_number'))
        if obj.direction == 'inbound':
            stops.reverse()
        points = [(s.fermata.longitude, s.fermata.latitude) for s in stops if s.fermata]
        geom = fetch_osrm_geometry(points)
        if geom and not obj.geometry:
            try:
                obj.geometry = geom
                obj.save(update_fields=['geometry'])
            except Exception:
                pass
        return geom or []

    def get_polyline(self, obj):
        return self.get_geometry(obj)

    def get_coordinates(self, obj):
        return self.get_geometry(obj)

        
#Route
class RouteSerializer(serializers.ModelSerializer):
    stops = RouteStopSerializer(many=True, read_only=True)
    directions = RouteDirectionSerializer(many=True, read_only=True)
    fermatas = serializers.SerializerMethodField()
    geometry = serializers.SerializerMethodField()
    polyline = serializers.SerializerMethodField()
    coordinates = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            'id',
            'public_bus_number',
            'name',
            'status',
            'created_at',
            'updated_at',
            'stops',
            'directions',
            'fermatas',
            'geometry',
            'polyline',
            'coordinates',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_fermatas(self, obj):
        # Returns ordered list of stops with fermata coordinates for map polyline rendering
        stops = obj.stops.all()
        return [
            {
                'id': stop.fermata.id,
                'name': stop.fermata.name,
                'latitude': stop.fermata.latitude,
                'longitude': stop.fermata.longitude,
                'sequence_number': stop.sequence_number,
                'order': stop.sequence_number,
            }
            for stop in stops
        ]

    def get_geometry(self, obj):
        # 1. First check outbound or any direction with stored geometry
        for direction in obj.directions.all():
            if direction.geometry and isinstance(direction.geometry, list) and len(direction.geometry) >= 2:
                return direction.geometry

        # 2. If no direction has stored geometry, compute from stops
        stops = obj.stops.all()
        points = [(s.fermata.longitude, s.fermata.latitude) for s in stops if s.fermata]
        return fetch_osrm_geometry(points) or []

    def get_polyline(self, obj):
        return self.get_geometry(obj)

    def get_coordinates(self, obj):
        return self.get_geometry(obj)
         
