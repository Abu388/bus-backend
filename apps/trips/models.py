

from django.db import models
from django.db.models import Q,F
from apps.core.models import BaseModel
from apps.routes.models import Route
from apps.fleet.models import PhysicalBus
from django.conf import settings

 
# Create your models here.

class Trip(BaseModel):

    class DirectionDisplay(models.TextChoices):
        OUTBOUND = 'outbound', 'Outbound'
        INBOUND = 'inbound', 'Inbound'
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'ended'


    route = models.ForeignKey(Route,on_delete=models.PROTECT,related_name='trips',)
    physical_bus = models.ForeignKey(PhysicalBus,on_delete=models.PROTECT,related_name='trips',)
    number = models.PositiveIntegerField(unique=True, editable=False)
    direction = models.CharField(max_length=50, choices=DirectionDisplay.choices)
    status = models.CharField(max_length=50,choices=Status.choices,default=Status.ACTIVE,)
    casher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='trips')
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    previous_trip = models.ForeignKey('self' ,on_delete=models.PROTECT, null=True, blank=True, related_name='next_trips' ) #the trip points to itself back

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['physical_bus'],
                condition=Q(status='active'),
                name='unique_active_trip_per_bus'
            ),
            models.UniqueConstraint(
                fields=['casher'],
                condition=Q(status='active'),
                name='unique_active_trip_per_casher'
            ),
            models.CheckConstraint(
                condition=Q(status='active', ended_at__isnull=True) | Q(status='ended', ended_at__isnull=False),
                name='trip_ended_at_matches_status'
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gte=F('started_at')),
                name='trip_ended_at_after_started_at'
            )     
        ]


        indexes = [
            models.Index(fields=['physical_bus', 'started_at']),
            models.Index(fields=['route', 'started_at']),
        ]


    def __str__(self):
        return f"Trip #{self.number} ({self.route.name})"

class TripQRSession(BaseModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        REVOKED = 'revoked', 'Revoked'
        ENDEDAT = 'expired', 'Expired'

    class Meta:
        constraints = [
            models.UniqueConstraint(

                fields=['trip'],
                condition=Q(status='active'),
                name='unique_active_qr_per_trip'
            )
        ]
        
        
       
    trip =  models.ForeignKey(Trip, on_delete=models.PROTECT , related_name='qr_sessions')
    token = models.CharField(unique=True, max_length=128)
    expires_at = models.DateTimeField()
    status =  models.CharField(max_length=50,choices=Status.choices,default=Status.ACTIVE,)

    def __str__(self):
        return f"QR for Trip #{self.trip.number} ({self.status})"