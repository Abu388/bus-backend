from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel
from apps.fares.models import Fare
from apps.trips.models import Trip


class Payment(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'
        EXPIRED = 'expired', 'Expired'

    trip = models.ForeignKey(
        Trip,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    passenger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    fare = models.ForeignKey(
        Fare,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.PENDING,
    )
    provider = models.CharField(max_length=50, default='mock')
    provider_ref = models.CharField(max_length=128, null=True, blank=True)
    idempotency_key = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            # 1. Amount must be strictly greater than 0
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='payment_amount_gt_zero',
            ),
            # 2. Unique (provider, provider_ref) when provider_ref is set
            models.UniqueConstraint(
                fields=['provider', 'provider_ref'],
                condition=Q(provider_ref__isnull=False),
                name='unique_provider_ref_when_set',
            ),
            # 3. Unique (passenger, idempotency_key) so retries never double-charge
            models.UniqueConstraint(
                fields=['passenger', 'idempotency_key'],
                name='unique_passenger_idempotency_key',
            ),
            # 4. Only one pending payment per passenger per trip
            models.UniqueConstraint(
                fields=['trip', 'passenger'],
                condition=Q(status='pending'),
                name='unique_pending_payment_per_passenger_trip',
            ),
            # 5. Only one succeeded payment per passenger per trip
            models.UniqueConstraint(
                fields=['trip', 'passenger'],
                condition=Q(status='succeeded'),
                name='unique_succeeded_payment_per_passenger_trip',
            ),
        ]
        indexes = [
            models.Index(fields=['trip', 'status', 'paid_at']),
        ]

    def __str__(self):
        return f"Payment {self.id} - {self.amount} ETB ({self.status})"


class ProviderEvent(BaseModel):
    provider = models.CharField(max_length=50)
    event_id = models.CharField(max_length=128)
    payload = models.JSONField()
    signature_valid = models.BooleanField(default=False)
    received_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name='provider_events',
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            # Ensures duplicate webhook deliveries from the same provider are caught
            models.UniqueConstraint(
                fields=['provider', 'event_id'],
                name='unique_provider_event_id',
            ),
        ]

    def __str__(self):
        return f"Webhook {self.provider}:{self.event_id} (valid={self.signature_valid})"