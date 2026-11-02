from django.db import transaction
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from apps.core.permissions import IsAdminOrReadOnly
from apps.fares.models import Fare
from apps.fares.serializers import FareSerializer


@extend_schema_view(
    get=extend_schema(
        tags=['Fares'],
        summary='List fares',
        description='Retrieve fare history or filter for current active fares.',
        parameters=[
            OpenApiParameter(
                name='route',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description='Filter fares by Route UUID',
                required=False,
            ),
            OpenApiParameter(
                name='active',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description='If true, returns only currently active fares (effective_to is null)',
                required=False,
            ),
        ],
    ),
    post=extend_schema(
        tags=['Fares'],
        summary='Configure a new route fare',
        description='Set a new fare for a route (Admin only). Automatically closes the previous active fare.',
    ),
)
class FareListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = FareSerializer

    def get_queryset(self):
        queryset = Fare.objects.select_related('route', 'configured_by').order_by('-effective_from')

        route_id = self.request.query_params.get('route')
        if route_id:
            queryset = queryset.filter(route_id=route_id)

        active = self.request.query_params.get('active')
        if active and active.lower() == 'true':
            queryset = queryset.filter(effective_to__isnull=True)

        return queryset

    def perform_create(self, serializer):
        with transaction.atomic():
            route = serializer.validated_data['route']
            new_effective_from = serializer.validated_data['effective_from']

            existing_active_fare = (
                Fare.objects.select_for_update()
                .filter(route=route, effective_to__isnull=True)
                .first()
            )

            if existing_active_fare:
                if new_effective_from <= existing_active_fare.effective_from:
                    raise ValidationError({
                        "effective_from": "New fare's effective_from must be after the existing active fare's effective_from."
                    })
                existing_active_fare.effective_to = new_effective_from
                existing_active_fare.save(update_fields=['effective_to', 'updated_at'])

            serializer.save(configured_by=self.request.user)