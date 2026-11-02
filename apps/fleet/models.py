from django.conf import settings
from django.db import models
from django.db.models import F, Q
from apps.core.models import BaseModel


class PhysicalBus(BaseModel):
    class BusStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        MAINTENANCE = 'maintenance', 'Maintenance'
        RETIRED = 'retired', 'Retired'

    targa = models.CharField(max_length=20, unique=True)
    route = models.ForeignKey(
        'routes.Route',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='buses',
    )
    status = models.CharField(
        max_length=50,
        choices=BusStatus.choices,
        default=BusStatus.ACTIVE,
    )

    def __str__(self):
        return self.targa


class MaintenanceEvent(BaseModel):
    bus = models.ForeignKey(
        PhysicalBus,
        on_delete=models.PROTECT,
        related_name='maintenance_events',
    )
    reason = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='maintenance_events_started',
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='maintenance_events_ended',
    )

    class Meta:
        constraints = [ #Prevents someone from accidentally entering an end time that happens before the start time 
            models.UniqueConstraint(
                fields=['bus'],
                condition=Q(ended_at__isnull=True),
                name='unique_open_maintenance_per_bus',
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=F('started_at')),
                name='maintenance_ended_at_after_started_at',
            ),
        ]

    def __str__(self):
        return f"{self.bus.targa} - {self.reason} ({self.started_at:%Y-%m-%d})"