from datetime import timedelta
import secrets
from apps.audit.models import AuditLog
from apps.audit.services import record

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsCasherRole
from apps.fares.models import Fare
from apps.fleet.models import PhysicalBus
from apps.tracking.models import BusLiveState
from apps.trips.models import Trip, TripQRSession
from apps.trips.serializers import (
    TripQRScanSerializer,
    TripQRSessionSerializer,
    TripSerializer,
)


# ============================================================================
# 1. LIST TRIPS (Admin/Cashier) & START TRIP (Cashier Only)
#    Endpoints: GET /trips/  |  POST /trips/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Trips'],
        summary='List trips (History & Active)',
        description='Retrieve trip history filtered by bus, status, or date (YYYY-MM-DD).',
        parameters=[
            OpenApiParameter(
                name='bus',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description='Filter trips by PhysicalBus UUID',
                required=False,
            ),
            OpenApiParameter(
                name='status',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                enum=['active', 'ended'],
                description='Filter trips by status (active or ended)',
                required=False,
            ),
            OpenApiParameter(
                name='date',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description='Filter trips by start date (Format: YYYY-MM-DD)',
                required=False,
            ),
        ],
    ),
    post=extend_schema(
        tags=['Trips'],
        summary='Start a new trip',
        description=(
            'Starts a new trip for the chosen physical_bus (Cashier only). '
            'Automatically copies the bus route, assigns the next trip number, '
            'defaults direction to the opposite of the last trip, and creates a QR session.'
        ),
    ),
)
class TripListCreateView(generics.ListCreateAPIView):
    # IsCasherRole allows Admin & Cashier to GET, and only Cashier to POST
    permission_classes = [IsCasherRole]
    serializer_class = TripSerializer

    def get_queryset(self):
        # Use select_related so foreign keys are fetched in 1 fast SQL query
        queryset = Trip.objects.select_related(
            'physical_bus', 'route', 'casher', 'previous_trip'
        ).order_by('-started_at')

        # Filter by bus UUID if ?bus=... is passed in the URL
        bus_id = self.request.query_params.get('bus')
        if bus_id:
            queryset = queryset.filter(physical_bus_id=bus_id)

        # Filter by status if ?status=active or ?status=ended is passed
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)

        # Filter by date if ?date=2026-10-31 is passed
        date_param = self.request.query_params.get('date')
        if date_param:
            queryset = queryset.filter(started_at__date=date_param)

        return queryset

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                # 1. Lock the bus row in PostgreSQL so two requests cannot start
                #    a trip on the same bus at the exact same millisecond (Race Condition protection)
                bus = PhysicalBus.objects.select_for_update().get(
                    id=serializer.validated_data['physical_bus'].id
                )

                # 2. Check that the bus is 'active' (not in maintenance or retired) and has a route
                if bus.status != PhysicalBus.BusStatus.ACTIVE:
                    raise ValidationError({"physical_bus": "Bus must be active to start a trip."})
                if not bus.route:
                    raise ValidationError({"physical_bus": "Bus does not have a route assigned."})

                # 3. Ensure neither the bus nor this cashier already has an open trip
                if Trip.objects.filter(physical_bus=bus, status=Trip.Status.ACTIVE).exists():
                    raise ValidationError({"physical_bus": "This bus already has an active trip."})
                if Trip.objects.filter(casher=self.request.user, status=Trip.Status.ACTIVE).exists():
                    raise ValidationError({"casher": "You already have an active trip."})

                # 4. Determine direction:
                #    If the cashier didn't send a direction, check the bus's last ended trip
                #    and flip it (outbound -> inbound, otherwise -> outbound).
                direction = serializer.validated_data.get('direction')
                if not direction:
                    last_trip = (
                        Trip.objects.filter(physical_bus=bus, status=Trip.Status.ENDED)
                        .order_by('-ended_at')
                        .first()
                    )
                    if last_trip and last_trip.direction == Trip.DirectionDisplay.OUTBOUND:
                        direction = Trip.DirectionDisplay.INBOUND
                    else:
                        direction = Trip.DirectionDisplay.OUTBOUND

                # 5. Calculate the next sequential human-readable trip number (starts at 501)
                last_num = Trip.objects.order_by('-number').values_list('number', flat=True).first()
                next_number = (last_num or 500) + 1

                # 6. Save the Trip using the backend server clock (timezone.now())
                now = timezone.now()
                trip = serializer.save(
                    route=bus.route,
                    casher=self.request.user,
                    number=next_number,
                    direction=direction,
                    status=Trip.Status.ACTIVE,
                    started_at=now,
                )

                # 7. Create the active QR Session in the exact same transaction
                TripQRSession.objects.create(
                    trip=trip,
                    token=secrets.token_urlsafe(32),
                    status=TripQRSession.Status.ACTIVE,
                    expires_at=now + timedelta(hours=12),
                )
                record(
                    actor=self.request.user,
                    action=AuditLog.Action.TRIP_STARTED,
                    entity_type='Trip',
                    entity_id=trip.id,
                    metadata={'bus_id': str(bus.id), 'route_id': str(bus.route_id), 'direction': trip.direction},
                )

        except IntegrityError:
            # Safety net if two simultaneous requests hit the database partial UniqueConstraint
            raise ValidationError(
                {"detail": "Conflict: This bus or cashier already has an active trip."}
            )


# ============================================================================
# 2. GET CASHIER'S CURRENT ACTIVE TRIP
#    Endpoint: GET /trips/active/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Trips'],
        summary='Get current active trip for logged-in cashier',
        description='Returns the cashier’s currently active trip (used when the mobile app restarts).',
    )
)
class ActiveTripView(generics.RetrieveAPIView):
    permission_classes = [IsCasherRole]
    serializer_class = TripSerializer

    def get_object(self):
        # Look up the active trip belonging to the logged-in cashier
        trip = (
            Trip.objects.select_related('physical_bus', 'route', 'casher', 'previous_trip')
            .filter(casher=self.request.user, status=Trip.Status.ACTIVE)
            .first()
        )
        if not trip:
            raise NotFound("No active trip found for this cashier.")
        return trip


# ============================================================================
# 3. END A TRIP (Cashier who started it OR Admin)
#    Endpoint: POST /trips/<uuid:pk>/end/
# ============================================================================
class EndTripView(APIView):
    # Both Cashiers and Admins are authenticated users; we check the exact role/owner inside post()
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Trips'],
        summary='End an active trip',
        description='Ends the trip and revokes its active QR session. Only allowed by the cashier who started it or an admin.',
        request=None,
        responses={200: TripSerializer},
    )
    def post(self, request, pk):
        with transaction.atomic():
            # 1. Fetch and lock the trip row so it cannot be ended twice concurrently
            trip = get_object_or_404(
                Trip.objects.select_for_update().select_related('physical_bus', 'route', 'casher'),
                pk=pk,
            )

            # 2. Rule: Only the cashier who started this trip OR an admin can end it
            if request.user != trip.casher and getattr(request.user, 'role', None) != 'admin':
                raise PermissionDenied("Only the cashier who started this trip or an admin can end it.")

            # 3. Rule: Ending an already-ended trip returns a clear error
            if trip.status == Trip.Status.ENDED:
                raise ValidationError({"detail": "This trip has already ended."})

            # 4. Set status to 'ended' and ended_at to server clock time
            now = timezone.now()
            trip.status = Trip.Status.ENDED
            trip.ended_at = now
            trip.save(update_fields=['status', 'ended_at', 'updated_at'])
            BusLiveState.objects.filter(bus=trip.physical_bus).update(trip=None)
            # 5. Revoke any active QR session for this trip in the same transaction
            TripQRSession.objects.filter(
                trip=trip, status=TripQRSession.Status.ACTIVE
            ).update(status=TripQRSession.Status.REVOKED, updated_at=now)
            record(
                actor=request.user,
                action=AuditLog.Action.TRIP_ENDED,
                entity_type='Trip',
                entity_id=trip.id,
                metadata={'bus_id': str(trip.physical_bus_id), 'ended_at': str(now)},
            )

        return Response(TripSerializer(trip).data, status=status.HTTP_200_OK)


# ============================================================================
# 4. REVERSE A TRIP (End Current Trip + Start Opposite Trip in 1 Transaction)
#    Endpoint: POST /trips/<uuid:pk>/reverse/
# ============================================================================
class ReverseTripView(APIView):
    permission_classes = [IsCasherRole]

    @extend_schema(
        tags=['Trips'],
        summary='Reverse an active trip (End + Start opposite direction)',
        description=(
            'Ends the current active trip, revokes its QR token, and immediately starts '
            'a new trip in the opposite direction with previous_trip linked and a fresh QR token.'
        ),
        request=None,
        responses={201: TripSerializer},
    )
    def post(self, request, pk):
        try:
            with transaction.atomic():
                # 1. Lock both the current trip and the physical bus
                current_trip = get_object_or_404(
                    Trip.objects.select_for_update().select_related('physical_bus', 'route', 'casher'),
                    pk=pk,
                )
                bus = PhysicalBus.objects.select_for_update().get(id=current_trip.physical_bus_id)

                # 2. Verify this cashier owns the trip and the trip is still active
                if current_trip.casher != request.user:
                    raise PermissionDenied("You can only reverse your own active trip.")
                if current_trip.status != Trip.Status.ACTIVE:
                    raise ValidationError({"detail": "Cannot reverse a trip that has already ended."})

                # 3. Verify the bus is still active and assigned to a route
                if bus.status != PhysicalBus.BusStatus.ACTIVE:
                    raise ValidationError({"physical_bus": "Bus is no longer active and cannot start a return trip."})
                if not bus.route:
                    raise ValidationError({"physical_bus": "Bus no longer has a route assigned."})

                now = timezone.now()

                # --- STEP A: END THE CURRENT TRIP & REVOKE ITS QR ---
                current_trip.status = Trip.Status.ENDED
                current_trip.ended_at = now
                current_trip.save(update_fields=['status', 'ended_at', 'updated_at'])

                TripQRSession.objects.filter(
                    trip=current_trip, status=TripQRSession.Status.ACTIVE
                ).update(status=TripQRSession.Status.REVOKED, updated_at=now)

                # --- STEP B: START THE NEW RETURN TRIP IN OPPOSITE DIRECTION ---
                if current_trip.direction == Trip.DirectionDisplay.OUTBOUND:
                    new_direction = Trip.DirectionDisplay.INBOUND
                else:
                    new_direction = Trip.DirectionDisplay.OUTBOUND

                last_num = Trip.objects.order_by('-number').values_list('number', flat=True).first()
                next_number = (last_num or 500) + 1

                new_trip = Trip.objects.create(
                    route=bus.route,
                    physical_bus=bus,
                    number=next_number,
                    direction=new_direction,
                    status=Trip.Status.ACTIVE,
                    casher=request.user,
                    started_at=now,
                    previous_trip=current_trip,  # Links the return trip back to the trip it reversed from!
                )

                # Create a brand-new QR session for the new trip
                TripQRSession.objects.create(
                    trip=new_trip,
                    token=secrets.token_urlsafe(32),
                    status=TripQRSession.Status.ACTIVE,
                    expires_at=now + timedelta(hours=12),
                )
                record(
                    actor=request.user,
                    action=AuditLog.Action.TRIP_ENDED,
                    entity_type='Trip',
                    entity_id=current_trip.id,
                    metadata={'reason': 'reversed', 'next_trip_id': str(new_trip.id)},
                )
                record(
                    actor=request.user,
                    action=AuditLog.Action.TRIP_STARTED,
                    entity_type='Trip',
                    entity_id=new_trip.id,
                    metadata={'previous_trip_id': str(current_trip.id), 'direction': new_trip.direction},
                )

            return Response(TripSerializer(new_trip).data, status=status.HTTP_201_CREATED)
        except IntegrityError:
            raise ValidationError({"detail": "Could not reverse trip due to a concurrent active trip conflict."})


# ============================================================================
# 5. DISPLAY TRIP QR SESSION (Cashier / Admin)
#    Endpoint: GET /trips/<uuid:pk>/qr/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Trips - QR'],
        summary='Get active QR session for a trip',
        description='Returns the current active QR token and scannable QR image URL for the trip.',
    )
)
class TripQRView(generics.RetrieveAPIView):
    permission_classes = [IsCasherRole]
    serializer_class = TripQRSessionSerializer

    def get_object(self):
        trip = get_object_or_404(Trip, pk=self.kwargs['pk'])

        # If the user is a cashier, make sure they are only viewing their own trip's QR
        if getattr(self.request.user, 'role', None) == 'cashier' and trip.casher != self.request.user:
            raise PermissionDenied("You can only view the QR session for your own trip.")

        qr_session = TripQRSession.objects.filter(
            trip=trip, status=TripQRSession.Status.ACTIVE
        ).first()

        if not qr_session:
            raise NotFound("No active QR session found for this trip.")

        return qr_session


# ============================================================================
# 6. PASSENGER QR SCAN (Public / Passenger)
#    Endpoint: POST /trip-qr/scan/
# ============================================================================
class TripQRScanView(APIView):
    # Passengers scanning the QR before paying do not need cashier/admin roles
    permission_classes = [AllowAny]
    serializer_class = TripQRScanSerializer

    @extend_schema(
        tags=['Trips - QR'],
        summary='Scan a trip QR token (Passenger)',
        description=(
            'Validates the scanned QR token and returns the trip summary for the passenger: '
            'public bus number, route name, direction, and current active fare. '
            'Does NOT expose cashier identity or bus targa.'
        ),
        request=TripQRScanSerializer,
        responses={
            200: OpenApiTypes.OBJECT,
        },
    )
    def post(self, request):
        # 1. Validate that {"token": "..."} was sent in the POST body
        serializer = TripQRScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token'].strip()

        # 2. Find the QR session and preload its trip and route
        qr_session = (
            TripQRSession.objects.select_related('trip', 'trip__route')
            .filter(token=token)
            .first()
        )

        # 3. Rule: Token must exist, session must be active & unexpired, and trip must be active
        now = timezone.now()
        if not qr_session:
            raise ValidationError({"token": "Invalid QR token."})
        if qr_session.status != TripQRSession.Status.ACTIVE:
            raise ValidationError({"token": "This QR code is no longer active."})
        if qr_session.expires_at <= now:
            raise ValidationError({"token": "This QR code has expired."})
        if qr_session.trip.status != Trip.Status.ACTIVE:
            raise ValidationError({"token": "This trip has already ended."})

        trip = qr_session.trip
        route = trip.route

        # 4. Look up the current active fare for this route
        active_fare = Fare.objects.filter(route=route, effective_to__isnull=True).first()

        # 5. Return ONLY passenger-safe summary fields (NO cashier identity, NO bus targa!)
        return Response(
            {
                "trip_id": trip.id,
                "trip_number": trip.number,
                "public_bus_number": route.public_bus_number,
                "route_name": route.name,
                "direction": trip.direction,
                "current_fare": str(active_fare.amount) if active_fare else None,
            },
            status=status.HTTP_200_OK,
        )