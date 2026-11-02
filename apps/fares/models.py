from django.conf import settings
from django.db import models
from django.db.models import F, Q
from apps.core.models import BaseModel


class Fare(BaseModel):
    route = models.ForeignKey(
        'routes.Route',
        on_delete=models.PROTECT,
        related_name='fares',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    configured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='configured_fares',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gt=F('effective_from')),
                name='fare_effective_to_after_effective_from',
            ),
            models.UniqueConstraint(
                fields=['route'],
                condition=Q(effective_to__isnull=True),
                name='unique_active_fare_per_route',
            ),
        ]

    def __str__(self):
        return f"{self.route.name} - {self.amount}"