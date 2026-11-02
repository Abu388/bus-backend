from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import BaseModel
from apps.fleet.models import PhysicalBus
from apps.routes.models import Fermata
from apps.trips.models import Trip


class BusLocation(BaseModel):
    bus = models.ForeignKey(
        PhysicalBus,
        on_delete=models.PROTECT,
        related_name='location_history',
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='location_history',
    )
    latitude = models.FloatField(
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)]
    )
    longitude = models.FloatField(
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)]
    )
    speed = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0)],
    )
    heading = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(360.0)],
    )
    accuracy = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0)],
    )
    recorded_at = models.DateTimeField()
    received_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=['bus', 'recorded_at']),
            models.Index(fields=['trip', 'recorded_at']),
        ]

    def __str__(self):
        return f"Location for {self.bus.targa} at {self.recorded_at}"


class BusLiveState(models.Model):
    bus = models.OneToOneField(
        PhysicalBus,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='live_state',
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_buses',
    )
    latitude = models.FloatField(
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)]
    )
    longitude = models.FloatField(
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)]
    )
    speed = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0)],
    )
    heading = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(360.0)],
    )
    recorded_at = models.DateTimeField()
    received_at = models.DateTimeField()
    distance_along_m = models.FloatField(default=0.0)
    next_fermata = models.ForeignKey(
        Fermata,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approaching_buses',
    )

    def __str__(self):
        return f"Live State for {self.bus.targa}"