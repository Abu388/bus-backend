from django.shortcuts import render

# Create your views here.

from decimal import Decimal
import zoneinfo

from django.conf import settings
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.serializers import (
    AuditLogSerializer,
    BusTripReportSerializer,
    FareHistorySerializer,
    MaintenanceStartInputSerializer,
)
from apps.audit.services import record
from apps.core.permissions import IsAdminRole, IsCasherRole
from apps.fares.models import Fare
from apps.fleet.models import MaintenanceEvent, PhysicalBus
from apps.fleet.serializers import MaintenanceEventSerializer
from apps.payments.models import Payment
from apps.tracking.models import BusLiveState
from apps.trips.models import Trip


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def annotate_trip_financials(queryset):
    """
    Annotates a Trip queryset in a single SQL query with:
    - passenger_count: count of SUCCEEDED payments on that trip
    - revenue: sum of payment.amount (historical price paid) for SUCCEEDED payments
    Pending, failed, and expired payments are strictly excluded.
    """
    succeeded_filter = Q(payments__status=Payment.Status.SUCCEEDED)
    return queryset.annotate(
        passenger_count=Count('payments', filter=succeeded_filter),
        revenue=Coalesce(
            Sum('payments__amount', filter=succeeded_filter),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
    )


# ============================================================================
# 1. BUS MAINTENANCE START (Cashier/Admin) & HISTORY (Admin)
#    Endpoints: POST /buses/<uuid:pk>/maintenance/  |  GET /buses/<uuid:pk>/maintenance/
# ============================================================================
class BusMaintenanceView(APIView):
    permission_classes = [IsCasherRole]

    @extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Get maintenance history for a bus (Admin only)',
        responses={200: MaintenanceEventSerializer(many=True)},
    )
    def get(self, request, pk):
        if getattr(request.user, 'role', None) != 'admin':
            raise PermissionDenied("Only admins can view bus maintenance history.")
        bus = get_object_or_404(PhysicalBus, pk=pk)
        events = (
            MaintenanceEvent.objects.select_related('physical_bus', 'started_by', 'ended_by')
            .filter(physical_bus=bus)
            .order_by('-started_at')
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(events, request)
        return paginator.get_paginated_response(
            MaintenanceEventSerializer(page, many=True).data
        )

    @extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Send a bus into maintenance (Cashier / Admin)',
        description=(
            'Requires a reason. Rejected if the bus has an active trip or an open maintenance event. '
            'Sets bus status to maintenance, creates MaintenanceEvent, and writes an AuditLog row.'
        ),
        request=MaintenanceStartInputSerializer,
        responses={201: MaintenanceEventSerializer},
    )
    def post(self, request, pk):
        serializer = MaintenanceStartInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data['reason'].strip()

        with transaction.atomic():
            bus = get_object_or_404(PhysicalBus.objects.select_for_update(), pk=pk)

            # 1. Reject if bus has an active trip
            if Trip.objects.filter(physical_bus=bus, status=Trip.Status.ACTIVE).exists():
                raise ValidationError(
                    {"detail": "Cannot put bus into maintenance while it has an active trip. End the trip first."}
                )

            # 2. Reject if bus already has an open maintenance event or is not active
            if (
                bus.status == PhysicalBus.BusStatus.MAINTENANCE
                or MaintenanceEvent.objects.filter(physical_bus=bus, ended_at__isnull=True).exists()
            ):
                raise ValidationError({"detail": "This bus is already in maintenance."})

            if bus.status == PhysicalBus.BusStatus.RETIRED:
                raise ValidationError({"detail": "Retired buses cannot be placed into maintenance."})

            now = timezone.now()
            old_status = bus.status
            bus.status = PhysicalBus.BusStatus.MAINTENANCE
            bus.save(update_fields=['status', 'updated_at'])

            # Ensure live tracking clears any trip pointer
            BusLiveState.objects.filter(bus=bus).update(trip=None)

            event = MaintenanceEvent.objects.create(
                physical_bus=bus,
                reason=reason,
                started_by=request.user,
                started_at=now,
            )

            # Write AuditLog in the same transaction
            record(
                actor=request.user,
                action=AuditLog.Action.BUS_MAINTENANCE_STARTED,
                entity_type='PhysicalBus',
                entity_id=bus.id,
                metadata={
                    'reason': reason,
                    'maintenance_event_id': str(event.id),
                    'before': {'status': old_status},
                    'after': {'status': bus.status},
                },
            )

        return Response(
            MaintenanceEventSerializer(event).data,
            status=status.HTTP_201_CREATED,
        )


# ============================================================================
# 2. RESUME BUS FROM MAINTENANCE (Admin Only)
#    Endpoint: POST /buses/<uuid:pk>/resume/
# ============================================================================
class BusResumeMaintenanceView(APIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=['Fleet - Maintenance'],
        summary='Resume a bus from maintenance back to active (Admin)',
        request=None,
        responses={200: MaintenanceEventSerializer},
    )
    def post(self, request, pk):
        with transaction.atomic():
            bus = get_object_or_404(PhysicalBus.objects.select_for_update(), pk=pk)
            open_event = (
                MaintenanceEvent.objects.select_for_update()
                .filter(physical_bus=bus, ended_at__isnull=True)
                .first()
            )
            if not open_event:
                raise ValidationError({"detail": "No open maintenance event found for this bus."})

            now = timezone.now()
            old_status = bus.status
            bus.status = PhysicalBus.BusStatus.ACTIVE
            bus.save(update_fields=['status', 'updated_at'])

            open_event.ended_by = request.user
            open_event.ended_at = now
            open_event.save(update_fields=['ended_by', 'ended_at', 'updated_at'])

            record(
                actor=request.user,
                action=AuditLog.Action.BUS_MAINTENANCE_ENDED,
                entity_type='PhysicalBus',
                entity_id=bus.id,
                metadata={
                    'maintenance_event_id': str(open_event.id),
                    'before': {'status': old_status},
                    'after': {'status': bus.status},
                },
            )

        return Response(
            MaintenanceEventSerializer(open_event).data,
            status=status.HTTP_200_OK,
        )


# ============================================================================
# 3. ADMIN BUS TRIPS REPORT ("Trip #501, 23 passengers, 575 ETB")
#    Endpoint: GET /admin/buses/<uuid:pk>/trips/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Admin - Reports & History'],
        summary='Get all trips for a bus with passenger count and revenue (Admin)',
    )
)
class AdminBusTripsReportView(generics.ListAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = BusTripReportSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        bus = get_object_or_404(PhysicalBus, pk=self.kwargs['pk'])
        qs = (
            Trip.objects.select_related('physical_bus', 'route')
            .filter(physical_bus=bus)
            .order_by('-started_at')
        )
        return annotate_trip_financials(qs)


# ============================================================================
# 4. ADMIN ALL TRIPS FILTERABLE REPORT
#    Endpoint: GET /admin/trips/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Admin - Reports & History'],
        summary='Filter all trips with passenger count and revenue (Admin)',
        parameters=[
            OpenApiParameter('bus', OpenApiTypes.UUID, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('route', OpenApiTypes.UUID, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('status', OpenApiTypes.STR, OpenApiParameter.QUERY, enum=['active', 'ended'], required=False),
            OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
        ],
    )
)
class AdminTripsListView(generics.ListAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = BusTripReportSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = Trip.objects.select_related('physical_bus', 'route').order_by('-started_at')

        bus_id = self.request.query_params.get('bus')
        if bus_id:
            qs = qs.filter(physical_bus_id=bus_id)

        route_id = self.request.query_params.get('route')
        if route_id:
            qs = qs.filter(route_id=route_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        date_from = self.request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(started_at__date__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            qs = qs.filter(started_at__date__lte=date_to)

        return annotate_trip_financials(qs)


# ============================================================================
# 5. ADMIN SUMMARY REPORT (Totals + Breakdown by Route, Day in Addis TZ, and Bus)
#    Endpoint: GET /admin/reports/summary/
# ============================================================================
class AdminReportsSummaryView(APIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=['Admin - Reports & History'],
        summary='Get revenue, passengers, and trip totals broken down by route, local day, and bus',
        parameters=[
            OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        local_tz = zoneinfo.ZoneInfo(getattr(settings, 'TIME_ZONE', 'Africa/Addis_Ababa'))

        payments_qs = Payment.objects.filter(status=Payment.Status.SUCCEEDED)
        trips_qs = Trip.objects.all()

        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        if date_from:
            payments_qs = payments_qs.filter(paid_at__date__gte=date_from)
            trips_qs = trips_qs.filter(started_at__date__gte=date_from)
        if date_to:
            payments_qs = payments_qs.filter(paid_at__date__lte=date_to)
            trips_qs = trips_qs.filter(started_at__date__lte=date_to)

        # 1. Overall Totals (Single DB Aggregate)
        overall = payments_qs.aggregate(
            total_passengers=Count('id'),
            total_revenue=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )

        # 2. Breakdown by Route (using historical Payment.amount, never current Fare)
        by_route_qs = (
            payments_qs.values(
                'trip__route_id',
                'trip__route__public_bus_number',
                'trip__route__name',
            )
            .annotate(
                passengers=Count('id'),
                revenue=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .order_by('-revenue')
        )

        # 3. Breakdown by Local Day in Africa/Addis_Ababa (so 23:30 EAT stays on that local day!)
        by_day_qs = (
            payments_qs.annotate(local_day=TruncDate('paid_at', tzinfo=local_tz))
            .values('local_day')
            .annotate(
                passengers=Count('id'),
                revenue=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .order_by('-local_day')
        )

        # 4. Breakdown by Physical Bus
        by_bus_qs = (
            payments_qs.values(
                'trip__physical_bus_id',
                'trip__physical_bus__targa',
            )
            .annotate(
                passengers=Count('id'),
                revenue=Coalesce(
                    Sum('amount'),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .order_by('-revenue')
        )

        return Response(
            {
                "totals": {
                    "trips_count": trips_qs.count(),
                    "passengers_count": overall['total_passengers'],
                    "revenue": str(overall['total_revenue']),
                },
                "by_route": [
                    {
                        "route_id": row['trip__route_id'],
                        "public_bus_number": row['trip__route__public_bus_number'],
                        "route_name": row['trip__route__name'],
                        "passengers": row['passengers'],
                        "revenue": str(row['revenue']),
                    }
                    for row in by_route_qs
                ],
                "by_day": [
                    {
                        "date": str(row['local_day']),
                        "passengers": row['passengers'],
                        "revenue": str(row['revenue']),
                    }
                    for row in by_day_qs
                ],
                "by_bus": [
                    {
                        "bus_id": row['trip__physical_bus_id'],
                        "targa": row['trip__physical_bus__targa'],
                        "passengers": row['passengers'],
                        "revenue": str(row['revenue']),
                    }
                    for row in by_bus_qs
                ],
            },
            status=status.HTTP_200_OK,
        )


# ============================================================================
# 6. ADMIN FARE HISTORY
#    Endpoint: GET /admin/fares/history/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Admin - Reports & History'],
        summary='Get complete fare history across all routes with creator and timestamps (Admin)',
    )
)
class AdminFareHistoryView(generics.ListAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = FareHistorySerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return Fare.objects.select_related('route', 'configured_by').order_by('-effective_from')

    
# ============================================================================
# 7. ADMIN AUDIT LOG LIST (Read-Only, Filterable, Paginated)
#    Endpoint: GET /admin/audit/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Admin - Audit'],
        summary='View append-only system audit logs (Admin)',
        parameters=[
            OpenApiParameter('actor', OpenApiTypes.UUID, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('action', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('entity_type', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('entity_id', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('date_from', OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('date_to', OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False),
        ],
    )
)
class AdminAuditLogListView(generics.ListAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = AuditLogSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = AuditLog.objects.select_related('actor').order_by('-created_at')

        actor_id = self.request.query_params.get('actor')
        if actor_id:
            qs = qs.filter(actor_id=actor_id)

        action = self.request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)

        entity_type = self.request.query_params.get('entity_type')
        if entity_type:
            qs = qs.filter(entity_type__iexact=entity_type)

        entity_id = self.request.query_params.get('entity_id')
        if entity_id:
            qs = qs.filter(entity_id=str(entity_id))

        date_from = self.request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        return qs