from django.db import models

# Create your models here.

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class AuditLog(BaseModel):
    class ActorKind(models.TextChoices):
        USER = 'user', 'User'
        SYSTEM = 'system', 'System'
        PROVIDER = 'provider', 'Provider'

    class Action(models.TextChoices):
        TRIP_STARTED = 'trip.started', 'Trip Started'
        TRIP_ENDED = 'trip.ended', 'Trip Ended'
        FARE_CHANGED = 'fare.changed', 'Fare Changed'
        BUS_MAINTENANCE_STARTED = 'bus.maintenance_started', 'Bus Maintenance Started'
        BUS_MAINTENANCE_ENDED = 'bus.maintenance_ended', 'Bus Maintenance Ended'
        ROUTE_UPDATED = 'route.updated', 'Route Updated'
        USER_ROLE_CHANGED = 'user.role_changed', 'User Role Changed'
        USER_SUSPENDED = 'user.suspended', 'User Suspended'
        PAYMENT_INITIATED = 'payment.initiated', 'Payment Initiated'
        PAYMENT_SUCCEEDED = 'payment.succeeded', 'Payment Succeeded'
        PAYMENT_FAILED = 'payment.failed', 'Payment Failed'
        TICKET_ISSUED = 'ticket.issued', 'Ticket Issued'
        TICKET_VALIDATED = 'ticket.validated', 'Ticket Validated'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    actor_kind = models.CharField(
        max_length=20,
        choices=ActorKind.choices,
        default=ActorKind.USER,
    )
    action = models.CharField(
        max_length=64,
        choices=Action.choices,
    )
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id', 'created_at']),
            models.Index(fields=['actor', 'created_at']),
            models.Index(fields=['action', 'created_at']),
        ]

    def __str__(self):
        return f"[{self.created_at}] {self.action} on {self.entity_type}:{self.entity_id}"