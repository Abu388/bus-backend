from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from apps.core.models import BaseModel


class Fermata(BaseModel):
    name = models.CharField(max_length=100)
    latitude = models.FloatField(
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)]
    )
    longitude = models.FloatField(
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)]
    )

    def __str__(self):
        return self.name


class Route(BaseModel):
    class RouteStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    public_bus_number = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=100, unique=True)
    status = models.CharField(
        max_length=50,
        choices=RouteStatus.choices,
        default=RouteStatus.ACTIVE,
    )

    def __str__(self):
        return f"{self.public_bus_number} - {self.name}"


class RouteStop(BaseModel):
    route = models.ForeignKey(Route,on_delete=models.CASCADE,related_name='stops',)
    fermata = models.ForeignKey(Fermata,on_delete=models.PROTECT,related_name='route_stops',)
    sequence_number = models.PositiveIntegerField()

    class Meta:
        ordering = ['sequence_number']
        constraints = [
            models.UniqueConstraint(fields=['route', 'fermata'],name='unique_route_fermata',),
            models.UniqueConstraint(fields=['route', 'sequence_number'],name='unique_route_sequence_number',),
        ]

    def __str__(self):
        return f"{self.route.name} - Stop #{self.sequence_number} ({self.fermata.name})"


class RouteDirection(BaseModel):
    class Direction(models.TextChoices):
        OUTBOUND = 'outbound', 'Outbound'
        INBOUND = 'inbound', 'Inbound'

    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name='directions',
    )
    direction = models.CharField(
        max_length=50,
        choices=Direction.choices,
    )
    # Stores OSRM coordinates as a list of [longitude, latitude] pairs
    geometry = models.JSONField(null=True, blank=True)
    distance_m = models.FloatField(null=True, blank=True)
    duration_s = models.FloatField(null=True, blank=True)
    stop_distances = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['route', 'direction'],
                name='unique_route_direction',
            ),
        ]

    def __str__(self):
        return f"{self.route.name} ({self.get_direction_display()})"