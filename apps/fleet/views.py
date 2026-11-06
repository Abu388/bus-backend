from django.db import transaction
from django.db.models import ProtectedError
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from apps.core.permissions import IsAdminOrCasherReadOnly, IsCasherRole
from apps.fleet.models import MaintenanceEvent, PhysicalBus
from apps.fleet.serializers import MaintenanceEventSerializer, PhysicalBusSerializer


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
        queryset = PhysicalBus.objects.select_related('route').all()

        route_id = self.request.query_params.get('route')
        if route_id:
            queryset = queryset.filter(route_id=route_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)

        return queryset


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
        description='Update specific fields of a physical bus such as route or status (Admin only).',
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
    queryset = PhysicalBus.objects.select_related('route').all()

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
        description='Open a new maintenance event for a bus (Casher only). Automatically sets bus status to maintenance.',
    ),
)
class MaintenanceEventListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsCasherRole]
    serializer_class = MaintenanceEventSerializer

    def get_queryset(self):
        queryset = MaintenanceEvent.objects.select_related(
            'bus', 'started_by', 'ended_by'
        ).order_by('-started_at')

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
        description='Update a maintenance event (Casher only).',
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