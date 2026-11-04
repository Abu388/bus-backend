from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.routes.models import RouteDirection, RouteStop
from apps.tracking.models import BusLiveState, BusLocation
from apps.tracking.serializers import (
    BusLiveStateSerializer,
    LocationPingInputSerializer,
)
from apps.tracking.utils import calculate_progress_and_next_stop, haversine_m
from apps.trips.models import Trip


# ============================================================================
# 1. LIVE BUSES ON THE MAP (Passenger sees active trips; Admin sees all)
#    Endpoint: GET /buses/live/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Tracking'],
        summary='Get all live buses',
        description='Passengers receive buses with an active trip only. Admins receive all buses (including idle and maintenance).',
    )
)
class LiveBusesListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = BusLiveStateSerializer

    def get_queryset(self):
        queryset = BusLiveState.objects.select_related('bus', 'trip', 'next_fermata').all()
        if getattr(self.request.user, 'role', None) == 'admin':
            return queryset
        return queryset.filter(trip__status='active', bus__status='active')


# ============================================================================
# 2. SEARCH LIVE BUSES BY PUBLIC ROUTE NUMBER
#    Endpoint: GET /buses/search/?number=124
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Tracking'],
        summary='Search live buses by public bus number',
        parameters=[
            OpenApiParameter(
                name='number',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description='Public bus number (e.g., 124)',
                required=True,
            ),
        ],
    )
)
class BusSearchByNumberView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = BusLiveStateSerializer

    def get_queryset(self):
        number = self.request.query_params.get('number')
        if not number:
            raise ValidationError("Please provide a bus number in the query (?number=...).")
        return BusLiveState.objects.select_related('bus', 'trip', 'next_fermata').filter(
            trip__route__public_bus_number=number,
            trip__status='active',
            bus__status='active',
        )


# ============================================================================
# 3. GET LAST BUS LOCATION (Any Logged-In User) & POST GPS PING (Casher)
#    Endpoints: GET /buses/<uuid:pk>/location/  |  POST /buses/<uuid:pk>/location/
# ============================================================================
class BusLocationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=['Tracking'],
        summary='Get last known location of a specific bus',
        responses={200: BusLiveStateSerializer},
    )
    def get(self, request, pk):
        live_state = (
            BusLiveState.objects.select_related('bus', 'trip', 'next_fermata')
            .filter(bus_id=pk)
            .first()
        )
        if not live_state:
            raise NotFound("No location data found for this bus.")
        return Response(BusLiveStateSerializer(live_state).data)

    @extend_schema(
        tags=['Tracking'],
        summary='Send GPS ping from Casher phone',
        description='Records GPS ping in history and updates live bus state if newer than existing state.',
        request=LocationPingInputSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request, pk):
        # 1. Validate incoming GPS coordinates, speed, heading, and recorded_at
        serializer = LocationPingInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 2. Check that the bus has an active trip
        active_trip = (
            Trip.objects.select_related('route', 'physical_bus')
            .filter(physical_bus_id=pk, status='active')
            .first()
        )
        if not active_trip:
            return Response(
                {"tracking_active": False, "detail": "No active trip for this bus."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Ensure the caller is the casher who owns this active trip
        if active_trip.casher != request.user:
            raise PermissionDenied("Only the casher of the active trip can send location pings.")

        now = timezone.now()

        with transaction.atomic():
            # 4. Always append the ping to the BusLocation history table
            BusLocation.objects.create(
                bus=active_trip.physical_bus,
                trip=active_trip,
                latitude=data['latitude'],
                longitude=data['longitude'],
                speed=data['speed'],
                heading=data.get('heading'),
                accuracy=data.get('accuracy'),
                recorded_at=data['recorded_at'],
                received_at=now,
            )

            # 5. Lock the live state row for this bus
            existing_live = (
                BusLiveState.objects.select_for_update().filter(bus_id=pk).first()
            )

            # 6. Only update BusLiveState if this is the first ping OR recorded_at is newer
            #    (Out-of-order older packets are kept in history but don't overwrite the live map)
            if not existing_live or data['recorded_at'] >= existing_live.recorded_at:
                dist_m, next_stop = calculate_progress_and_next_stop(
                    active_trip, data['latitude'], data['longitude']
                )
                BusLiveState.objects.update_or_create(
                    bus=active_trip.physical_bus,
                    defaults={
                        'trip': active_trip,
                        'latitude': data['latitude'],
                        'longitude': data['longitude'],
                        'speed': data['speed'],
                        'heading': data.get('heading'),
                        'recorded_at': data['recorded_at'],
                        'received_at': now,
                        'distance_along_m': dist_m,
                        'next_fermata': next_stop,
                    },
                )

        return Response({"tracking_active": True}, status=status.HTTP_200_OK)


# ============================================================================
# 4. GET UPCOMING STOPS & ETAs FOR A BUS
#    Endpoint: GET /buses/<uuid:pk>/eta/
# ============================================================================
class BusETAView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=['Tracking'],
        summary='Get next fermata and ETA for all upcoming stops',
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, pk):
        live_state = (
            BusLiveState.objects.select_related('bus', 'trip', 'trip__route', 'next_fermata')
            .filter(bus_id=pk)
            .first()
        )

        if not live_state or not live_state.trip or live_state.trip.status != 'active':
            raise NotFound("This bus does not have an active trip for ETA calculation.")

        # 1. Check 10-second cache first so we don't recalculate on every passenger read
        cache_key = f"trip_eta_{live_state.trip_id}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data)

        # 2. Determine speed (m/s): fallback to 5.5 m/s (~20 km/h) if 0/missing, minimum floor 2.0 m/s
        if live_state.speed and live_state.speed > 0:
            effective_speed = max(live_state.speed, 2.0)
        else:
            effective_speed = 5.5

        trip = live_state.trip
        route_dir = RouteDirection.objects.filter(
            route=trip.route, direction=trip.direction
        ).first()
        stop_distances = (route_dir.stop_distances or {}) if route_dir else {}

        # 3. Get ordered stops for this trip's direction
        stops = list(
            RouteStop.objects.filter(route=trip.route)
            .select_related('fermata')
            .order_by('sequence_number')
        )
        if trip.direction == 'inbound':
            stops.reverse()

        # 4. Calculate cumulative distance to each stop and ETA for stops ahead of the bus
        upcoming_stops = []
        cumulative_m = 0.0
        dwell_time_per_stop_s = 20  # 20 seconds pause per intermediate stop

        for idx, stop in enumerate(stops):
            if idx > 0:
                prev_f = stops[idx - 1].fermata
                curr_f = stop.fermata
                cumulative_m += haversine_m(
                    prev_f.latitude, prev_f.longitude, curr_f.latitude, curr_f.longitude
                )

            # Use stored stop_distance if available, otherwise fallback to cumulative straight-line distance
            stop_dist_m = float(stop_distances.get(str(stop.fermata_id), cumulative_m))

            # If this stop is ahead of the bus's current distance_along_m
            if stop_dist_m > live_state.distance_along_m:
                remaining_m = stop_dist_m - live_state.distance_along_m
                stops_between = len(upcoming_stops)
                eta_seconds = int(
                    (remaining_m / effective_speed) + (stops_between * dwell_time_per_stop_s)
                )
                upcoming_stops.append(
                    {
                        "fermata_id": stop.fermata.id,
                        "fermata_name": stop.fermata.name,
                        "sequence_number": stop.sequence_number,
                        "distance_remaining_m": round(remaining_m, 1),
                        "eta_seconds": eta_seconds,
                    }
                )

        result_data = {
            "bus_id": live_state.bus_id,
            "trip_id": trip.id,
            "direction": trip.direction,
            "distance_along_m": live_state.distance_along_m,
            "next_fermata": (
                live_state.next_fermata.name if live_state.next_fermata else None
            ),
            "upcoming_stops": upcoming_stops,
        }

        # 5. Cache for 10 seconds per trip
        cache.set(cache_key, result_data, timeout=10)
        return Response(result_data)
    