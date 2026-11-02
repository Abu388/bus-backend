from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from apps.core.permissions import IsAdminOrReadOnly
from apps.routes.models import Fermata, Route, RouteDirection, RouteStop
from apps.routes.serializers import (
    FermataSerializer,
    RouteDirectionSerializer,
    RouteSerializer,
    RouteStopSerializer,
)


@extend_schema_view(
    get=extend_schema(tags=['Routes - Fermatas'], summary="List all fermatas"),
    post=extend_schema(tags=['Routes - Fermatas'], summary="Create a new fermata"),
)
class FermataListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = FermataSerializer

    def get_queryset(self):
        queryset = Fermata.objects.all()
        return queryset


@extend_schema_view(
    get=extend_schema(tags=['Routes - Fermatas'], summary="Retrieve a fermata"),
    put=extend_schema(tags=['Routes - Fermatas'], summary="Update a fermata"),
    patch=extend_schema(tags=['Routes - Fermatas'], summary="Partially update a fermata"),
    delete=extend_schema(tags=['Routes - Fermatas'], summary="Delete a fermata (Protected if in use)"),
)
class FermataDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = FermataSerializer 

    def get_queryset(self):
        queryset = Fermata.objects.all()
        return queryset
    
    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError: 
            raise ValidationError("Cannot delete this fermata because it is currently used as a stop on a route.")


@extend_schema_view(
    get=extend_schema(tags=['Routes - Routes'], summary="List all bus routes"),
    post=extend_schema(tags=['Routes - Routes'], summary="Create a new bus route"),
)
class RouteListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = RouteSerializer

    def get_queryset(self):
        queryset = Route.objects.prefetch_related('stops__fermata', 'directions').all()
        return queryset


@extend_schema_view(
    get=extend_schema(tags=['Routes - Routes'], summary="Retrieve route details, stops, and geometry"),
    put=extend_schema(tags=['Routes - Routes'], summary="Update a route"),
    patch=extend_schema(tags=['Routes - Routes'], summary="Partially update a route"),
    delete=extend_schema(tags=['Routes - Routes'], summary="Delete a route (Protected if buses/fares attached)"),
)
class RouteDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = RouteSerializer

    def get_queryset(self):
        queryset = Route.objects.prefetch_related('stops__fermata', 'directions').all()
        return queryset
    
    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError("Cannot delete a route that has buses or fares attached to it. Set its status to inactive instead.")


@extend_schema_view(
    get=extend_schema(tags=['Routes - Stops'], summary="List route stops"),
    post=extend_schema(tags=['Routes - Stops'], summary="Add a fermata stop to a route"),
)
class RouteStopListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = RouteStopSerializer

    def get_queryset(self):
        queryset = RouteStop.objects.select_related('route', 'fermata').all()
        return queryset

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError("Stop, sequence number, or fermata already exists for this route!")


@extend_schema_view(
    get=extend_schema(tags=['Routes - Stops'], summary="Retrieve a route stop"),
    put=extend_schema(tags=['Routes - Stops'], summary="Update a route stop"),
    patch=extend_schema(tags=['Routes - Stops'], summary="Partially update a route stop"),
    delete=extend_schema(tags=['Routes - Stops'], summary="Delete a route stop"),
)

class RouteStopDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = RouteStopSerializer 

    def get_queryset(self):
        queryset = RouteStop.objects.select_related('route', 'fermata').all()
        return queryset
   

@extend_schema_view(
    get=extend_schema(tags=['Routes - Directions'], summary="List route directions (OSRM paths)"),
    post=extend_schema(tags=['Routes - Directions'], summary="Create a route direction geometry"),
)
class RouteDirectionListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = RouteDirectionSerializer
    
    def get_queryset(self):
        queryset = RouteDirection.objects.select_related('route').all()
        return queryset

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError("This route already has a direction configured for this type (outbound/inbound)!")


@extend_schema_view(
    get=extend_schema(tags=['Routes - Directions'], summary="Retrieve route direction details"),
    put=extend_schema(tags=['Routes - Directions'], summary="Update route direction data"),
    patch=extend_schema(tags=['Routes - Directions'], summary="Partially update route direction geometry/duration"),
    delete=extend_schema(tags=['Routes - Directions'], summary="Delete a route direction"),
)
class RouteDirectionDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = RouteDirectionSerializer

    def get_queryset(self):
        queryset = RouteDirection.objects.select_related('route').all()
        return queryset