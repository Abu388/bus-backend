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
        queryset = BusLiveState.objects.select_related('bus', 'bus__route', 'trip', 'trip__route', 'next_fermata').all()
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
        if not number.isdigit():
            return BusLiveState.objects.none()
        return BusLiveState.objects.select_related('bus', 'bus__route', 'trip', 'trip__route', 'next_fermata').filter(
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
            BusLiveState.objects.select_related('bus', 'bus__route', 'trip', 'trip__route', 'next_fermata')
            .filter(bus_id=pk)
            .first()
        )
        if not live_state:
            raise NotFound("No location data found for this bus.")
        if live_state.trip is None or getattr(live_state.trip, 'status', None) != 'active':
            current_active = Trip.objects.filter(physical_bus_id=pk, status='active').first()
            if current_active:
                live_state.trip = current_active
                live_state.save(update_fields=['trip'])
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
                live_state, _ = BusLiveState.objects.update_or_create(
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
            else:
                live_state = existing_live

            # Automatically look up and link active trip if missing or inactive
            if live_state.trip is None or getattr(live_state.trip, 'status', None) != 'active':
                current_active = Trip.objects.filter(physical_bus=active_trip.physical_bus, status='active').first()
                if current_active:
                    live_state.trip = current_active
                    live_state.save(update_fields=['trip'])

        return Response({"tracking_active": True}, status=status.HTTP_200_OK)


# ============================================================================
# 4. GET UPCOMING STOPS & ETAs FOR A BUS
#    Endpoint: GET /buses/<uuid:pk>/eta/
# ============================================================================
class BusETAView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Tracking'],
        summary='Get next fermata and ETA for all upcoming stops',
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, pk):
        live_state = (
            BusLiveState.objects.select_related('bus', 'bus__route', 'trip', 'trip__route', 'next_fermata')
            .filter(bus_id=pk)
            .first()
        )

        bus = None
        if not live_state:
            bus = PhysicalBus.objects.select_related('route').filter(id=pk).first()
            if not bus:
                raise NotFound("Bus not found.")

        # If live_state has no active trip, look up currently active trip for this bus
        active_trip = None
        if live_state:
            if live_state.trip and getattr(live_state.trip, 'status', None) == 'active':
                active_trip = live_state.trip
            else:
                active_trip = (
                    Trip.objects.select_related('route', 'physical_bus')
                    .filter(physical_bus_id=pk, status='active')
                    .first()
                )
                if active_trip:
                    live_state.trip = active_trip
                    try:
                        live_state.save(update_fields=['trip'])
                    except Exception:
                        pass
        else:
            active_trip = (
                Trip.objects.select_related('route', 'physical_bus')
                .filter(physical_bus_id=pk, status='active')
                .first()
            )

        # Determine route, trip, and direction
        if active_trip:
            trip_id = active_trip.id
            route = active_trip.route
            direction = active_trip.direction
        elif live_state and live_state.bus and live_state.bus.route:
            trip_id = None
            route = live_state.bus.route
            direction = 'outbound'
        elif bus and bus.route:
            trip_id = None
            route = bus.route
            direction = 'outbound'
        else:
            raise NotFound("This bus does not have an active trip or assigned route for ETA calculation.")

        # 1. Check 5-second cache
        cache_key = f"trip_eta_{trip_id or pk}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data)

        # 2. Determine speed (m/s): if speed is 0 or very low (< 1.5 m/s), fall back to realistic 6.0 m/s (~21.6 km/h)
        speed = live_state.speed if live_state else 0.0
        if speed and speed >= 1.5:
            effective_speed = speed
        else:
            effective_speed = 6.0

        route_dir = RouteDirection.objects.filter(
            route=route, direction=direction
        ).first()
        stop_distances = (route_dir.stop_distances or {}) if route_dir else {}

        # 3. Get ordered stops for this trip's direction
        stops = list(
            RouteStop.objects.filter(route=route)
            .select_related('fermata')
            .order_by('sequence_number')
        )
        if direction == 'inbound':
            stops.reverse()

        # 4. Calculate cumulative distance to each stop and ETA for stops ahead of the bus
        upcoming_stops = []
        cumulative_m = 0.0
        dwell_time_per_stop_s = 20  # 20 seconds pause per intermediate stop
        current_distance = live_state.distance_along_m if live_state else 0.0

        for idx, stop in enumerate(stops):
            if idx > 0:
                prev_f = stops[idx - 1].fermata
                curr_f = stop.fermata
                cumulative_m += haversine_m(
                    prev_f.latitude, prev_f.longitude, curr_f.latitude, curr_f.longitude
                )

            stop_dist_m = float(stop_distances.get(str(stop.fermata_id), cumulative_m))

            # Include stops ahead of current progress
            if stop_dist_m > current_distance:
                remaining_m = max(stop_dist_m - current_distance, 50.0)
                stops_between = len(upcoming_stops)
                eta_seconds = int(
                    (remaining_m / effective_speed) + (stops_between * dwell_time_per_stop_s)
                )
                eta_minutes = round(eta_seconds / 60.0, 1)

                upcoming_stops.append(
                    {
                        "id": stop.fermata.id,
                        "fermata_id": stop.fermata.id,
                        "name": stop.fermata.name,
                        "sequence_number": stop.sequence_number,
                        "latitude": stop.fermata.latitude,
                        "longitude": stop.fermata.longitude,
                        "distance_m": round(remaining_m, 1),
                        "distance_remaining_m": round(remaining_m, 1),
                        "eta_seconds": eta_seconds,
                        "eta_minutes": eta_minutes,
                    }
                )

        # If bus is at or past all stops, fallback to final stop
        if not upcoming_stops and stops:
            last_stop = stops[-1]
            upcoming_stops.append(
                {
                    "id": last_stop.fermata.id,
                    "fermata_id": last_stop.fermata.id,
                    "name": last_stop.fermata.name,
                    "sequence_number": last_stop.sequence_number,
                    "latitude": last_stop.fermata.latitude,
                    "longitude": last_stop.fermata.longitude,
                    "distance_m": 0.0,
                    "distance_remaining_m": 0.0,
                    "eta_seconds": 0,
                    "eta_minutes": 0.0,
                }
            )

        # Next fermata object
        if upcoming_stops:
            first_stop = upcoming_stops[0]
            next_fermata = {
                "id": first_stop["id"],
                "name": first_stop["name"],
                "eta_seconds": first_stop["eta_seconds"],
                "eta_minutes": first_stop["eta_minutes"],
                "distance_remaining_m": first_stop["distance_remaining_m"],
            }
        else:
            next_fermata = None

        result_data = {
            "bus_id": pk,
            "trip_id": trip_id,
            "route_id": route.id,
            "public_bus_number": route.public_bus_number,
            "route_name": route.name,
            "direction": direction,
            "distance_along_m": current_distance,
            "speed": speed,
            "effective_speed": effective_speed,
            "next_fermata": next_fermata,
            "upcoming_stops": upcoming_stops,
            "stops": upcoming_stops,
        }

        # 5. Cache for 5 seconds
        cache.set(cache_key, result_data, timeout=5)
        return Response(result_data)
    