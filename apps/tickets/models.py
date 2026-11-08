from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.core.models import BaseModel
from apps.payments.models import Payment
from apps.trips.models import Trip


class Ticket(BaseModel):
    payment = models.OneToOneField(
        Payment,
        on_delete=models.PROTECT,
        related_name='ticket',
    )
    passenger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='tickets',
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.PROTECT,
        related_name='tickets',
    )
    code = models.CharField(max_length=20)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-issued_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=F('issued_at')),
                name='ticket_expires_after_issued',
            )
        ]

    def get_computed_status(self) -> str:
        """
        Returns 'used' if ticket was validated/scanned by a casher.
        Valid only if the trip is still active AND current server time is before expires_at.
        Otherwise 'expired'.
        """
        if hasattr(self, 'scans') and self.scans.filter(result='valid').exists():
            return 'used'
        if self.trip.status == Trip.Status.ACTIVE and timezone.now() < self.expires_at:
            return 'valid'
        return 'expired'

    def __str__(self):
        return f"Ticket {self.code} ({self.get_computed_status()})"


class TicketScan(BaseModel):
    class Result(models.TextChoices):
        VALID = 'valid', 'Valid'
        INVALID = 'invalid', 'Invalid'
        EXPIRED = 'expired', 'Expired'
        WRONG_TRIP = 'wrong_trip', 'Wrong Trip'

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='scans',
    )
    casher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ticket_scans',
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ticket_scans',
    )
    result = models.CharField(max_length=20, choices=Result.choices)
    scanned_at = models.DateTimeField()

    class Meta:
        ordering = ['-scanned_at']

    def __str__(self):
        return f"Scan by {self.casher_id} -> {self.result} at {self.scanned_at}"