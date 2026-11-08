from datetime import timedelta
import secrets

from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.core.permissions import IsAdminOrCasherReadOnly, IsCasherRole
from apps.fares.models import Fare
from apps.fleet.models import MaintenanceEvent, PhysicalBus
from apps.fleet.serializers import MaintenanceEventSerializer, PhysicalBusSerializer
from apps.tracking.models import BusLiveState
from apps.trips.models import Trip, TripQRSession


def _end_active_trip(trip: Trip, actor=None):
    """Cleanly ends an active trip, revokes QR session, clears live state, and writes audit log."""
    now = timezone.now()
    trip.status = Trip.Status.ENDED
    trip.ended_at = now
    trip.save(update_fields=['status', 'ended_at', 'updated_at'])
    TripQRSession.objects.filter(
        trip=trip, status=TripQRSession.Status.ACTIVE
    ).update(status=TripQRSession.Status.REVOKED, updated_at=now)
    BusLiveState.objects.filter(bus=trip.physical_bus).update(trip=None)
    record(
        actor=actor,
        action=AuditLog.Action.TRIP_ENDED,
        entity_type='Trip',
        entity_id=trip.id,
        metadata={'bus_id': str(trip.physical_bus_id), 'ended_at': str(now)},
    )


def _sync_bus_trip_for_casher(bus, old_casher_id, actor):
    """
    Synchronizes Trip lifecycle with casher assignment:
    Step A: Ends active trip if casher is unassigned, replaced, or bus is not active.
    Step B: Starts new trip if casher is assigned, bus is active with route, and active fare exists.
    """
    # Step A — Auto-End Active Trip When Casher Is Unassigned, Replaced, or Bus Is Not Active
    if (
        (old_casher_id and (bus.casher_id != old_casher_id or bus.casher_id is None))
        or bus.status != PhysicalBus.BusStatus.ACTIVE
    ):
        active_trip = Trip.objects.filter(
            physical_bus=bus, status=Trip.Status.ACTIVE
        ).first()
        if active_trip:
            _end_active_trip(active_trip, actor=actor)

    # Step B — Auto-Start Trip When a Casher Is Assigned and All Requirements Are Met
    if (
        bus.casher_id is not None
        and bus.status == PhysicalBus.BusStatus.ACTIVE
        and bus.route_id is not None
    ):
        has_active_fare = Fare.objects.filter(
            route_id=bus.route_id,
            effective_to__isnull=True,
        ).exists()

        if has_active_fare:
            # If bus.casher had an orphaned active trip on a different bus, end that old trip first
            orphaned_trip = Trip.objects.filter(
                casher_id=bus.casher_id,
                status=Trip.Status.ACTIVE,
            ).exclude(physical_bus=bus).first()
            if orphaned_trip:
                _end_active_trip(orphaned_trip, actor=actor)

            # Check that neither bus nor bus.casher already has an active trip
            bus_has_active = Trip.objects.filter(
                physical_bus=bus, status=Trip.Status.ACTIVE
            ).exists()
            casher_has_active = Trip.objects.filter(
                casher_id=bus.casher_id, status=Trip.Status.ACTIVE
            ).exists()

            if not bus_has_active and not casher_has_active:
                last_trip = (
                    Trip.objects.filter(physical_bus=bus, status=Trip.Status.ENDED)
                    .order_by('-ended_at')
                    .first()
                )
                if last_trip and last_trip.direction == Trip.DirectionDisplay.OUTBOUND:
                    direction = Trip.DirectionDisplay.INBOUND
                else:
                    direction = Trip.DirectionDisplay.OUTBOUND

                last_num = (
                    Trip.objects.order_by('-number')
                    .values_list('number', flat=True)
                    .first()
                )
                next_number = (last_num or 500) + 1
                now = timezone.now()

                trip = Trip.objects.create(
                    route_id=bus.route_id,
                    physical_bus=bus,
                    casher_id=bus.casher_id,
                    number=next_number,
                    direction=direction,
                    status=Trip.Status.ACTIVE,
                    started_at=now,
                )

                TripQRSession.objects.create(
                    trip=trip,
                    token=secrets.token_urlsafe(32),
                    status=TripQRSession.Status.ACTIVE,
                    expires_at=now + timedelta(hours=12),
                )

                record(
                    actor=actor,
                    action=AuditLog.Action.TRIP_STARTED,
                    entity_type='Trip',
                    entity_id=trip.id,
                    metadata={
                        'bus_id': str(bus.id),
                        'route_id': str(bus.route_id),
                        'direction': trip.direction,
                    },
                )


@extend_schema_view(
    get=extend_schema(
        tags=['Fleet - Buses'],
        summary='List all physical buses',
        description='Retrieve a list of all physical buses. Can be filtered by route or status.',
        parameters=[
            OpenApiParameter(
                name='route',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description='Filter buses by Route UUID',
                required=False,
            ),
            OpenApiParameter(
                name='status',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                enum=['active', 'maintenance', 'retired'],
                description='Filter buses by status',
                required=False,
            ),
        ],
    ),
    post=extend_schema(
        tags=['Fleet - Buses'],
        summary='Register a new physical bus',
        description='Create a new physical bus (Admin only). Route assignment is optional.',
    ),
)
class PhysicalBusListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminOrCasherReadOnly]
    serializer_class = PhysicalBusSerializer

    def get_queryset(self):
        queryset = PhysicalBus.objects.select_related('route', 'casher').all()

        if getattr(self.request.user, 'role', None) == 'casher':
            queryset = queryset.filter(casher=self.request.user)

        route_id = self.request.query_params.get('route')
        if route_id:
            queryset = queryset.filter(route_id=route_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)

        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            bus = serializer.save()
            _sync_bus_trip_for_casher(bus, old_casher_id=None, actor=self.request.user)


@extend_schema_view(
    get=extend_schema(
        tags=['Fleet - Buses'],
        summary='Retrieve a specific bus',
        description='Get details of a single physical bus by its UUID.',
    ),
    put=extend_schema(
        tags=['Fleet - Buses'],
        summary='Update a physical bus',
        description='Full update of a physical bus (Admin only).',
    ),
    patch=extend_schema(
        tags=['Fleet - Buses'],
        summary='Partially update a physical bus',
        description='Update specific fields of a physical bus such as route, casher, or status (Admin only).',
    ),
    delete=extend_schema(
        tags=['Fleet - Buses'],
        summary='Delete a physical bus',
        description='Delete a physical bus if it has no maintenance history. Otherwise, set status to retired.',
    ),
)
class PhysicalBusDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminOrCasherReadOnly]
    serializer_class = PhysicalBusSerializer
    queryset = PhysicalBus.objects.select_related('route', 'casher').all()

    def perform_update(self, serializer):
        old_casher_id = serializer.instance.casher_id
        with transaction.atomic():
            bus = serializer.save()
            _sync_bus_trip_for_casher(bus, old_casher_id=old_casher_id, actor=self.request.user)

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError(
                {"detail": "Cannot delete a bus that has maintenance records. Set its status to retired instead."}
            )


@extend_schema_view(
    get=extend_schema(
        tags=['Fleet - Maintenance'],
        summary='List maintenance events',
        description='Retrieve maintenance logs (Accessible by Admin and Casher).',
        parameters=[
            OpenApiParameter(
                name='bus',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description='Filter maintenance events by PhysicalBus UUID',
                required=False,
            ),
            OpenApiParameter(
                name='open',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description='If true, returns only currently open maintenance events (ended_at is null)',
                required=False,
            ),
        ],
    ),
    post=extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Start a maintenance event',
        description='Open a new maintenance event for a bus (Casher/Admin). Automatically sets bus status to maintenance.',
    ),
)
class MaintenanceEventListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsCasherRole]
    serializer_class = MaintenanceEventSerializer

    def get_queryset(self):
        queryset = MaintenanceEvent.objects.select_related(
            'bus', 'started_by', 'ended_by'
        ).order_by('-started_at')

        if getattr(self.request.user, 'role', None) == 'casher':
            queryset = queryset.filter(bus__casher=self.request.user)

        bus_id = self.request.query_params.get('bus')
        if bus_id:
            queryset = queryset.filter(bus_id=bus_id)

        open_only = self.request.query_params.get('open')
        if open_only and open_only.lower() == 'true':
            queryset = queryset.filter(ended_at__isnull=True)

        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            bus = serializer.validated_data['bus']

            if getattr(self.request.user, 'role', None) == 'casher' and bus.casher_id != self.request.user.id:
                raise ValidationError(
                    {"bus": "You can only report maintenance for your own assigned bus."}
                )

            if bus.status == PhysicalBus.BusStatus.RETIRED:
                raise ValidationError(
                    {"bus": "Unable to create a maintenance event on a retired bus."}
                )

            if MaintenanceEvent.objects.select_for_update().filter(
                bus=bus, ended_at__isnull=True
            ).exists():
                raise ValidationError(
                    {"bus": "This bus is already in maintenance."}
                )

            bus.status = PhysicalBus.BusStatus.MAINTENANCE
            bus.save(update_fields=['status', 'updated_at'])

            active_trip = Trip.objects.filter(physical_bus=bus, status=Trip.Status.ACTIVE).first()
            if active_trip:
                _end_active_trip(active_trip, actor=self.request.user)

            serializer.save(started_by=self.request.user)


@extend_schema_view(
    get=extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Retrieve a maintenance event',
        description='Get details of a single maintenance event by its UUID.',
    ),
    put=extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Update a maintenance event',
        description='Update a maintenance event (Casher/Admin).',
    ),
    patch=extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Close or update a maintenance event',
        description='Send ended_at to close an open maintenance event and automatically restore the bus status to active.',
    ),
)
class MaintenanceEventDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsCasherRole]
    serializer_class = MaintenanceEventSerializer
    queryset = MaintenanceEvent.objects.select_related('bus', 'started_by', 'ended_by').all()

    def perform_update(self, serializer):
        instance = serializer.instance
        bus = instance.bus

        if instance.ended_at is not None:
            raise ValidationError({"detail": "This maintenance event is already closed."})

        with transaction.atomic():
            if serializer.validated_data.get('ended_at'):
                serializer.save(ended_by=self.request.user)
                bus.status = PhysicalBus.BusStatus.ACTIVE
                bus.save(update_fields=['status', 'updated_at'])
            else:
                serializer.save()