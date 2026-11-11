from django.shortcuts import render

# Create your views here.
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiTypes, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.core.permissions import IsCasherRole
from apps.tickets.models import Ticket, TicketScan
from apps.tickets.serializers import TicketSerializer, TicketValidateInputSerializer
from apps.tickets.services import (
    generate_rotating_ticket_token,
    verify_rotating_ticket_token,
)
from apps.trips.models import Trip


def mask_passenger_name(user) -> str:
    raw = (
        getattr(user, 'full_name', None)
        or getattr(user, 'phone_number', None)
        or getattr(user, 'username', None)
        or str(user.id)
    )
    raw_str = str(raw).strip()
    if len(raw_str) <= 3:
        return f"{raw_str[0]}***"
    return f"{raw_str[:2]}***{raw_str[-2:]}"


# ============================================================================
# 1. LIST MY TICKETS (Passenger History, Newest First)
#    Endpoint: GET /tickets/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Tickets'],
        summary='Get my ticket history (Passenger)',
        description='Returns the logged-in passenger’s tickets, newest first, with computed status.',
    )
)
class TicketListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TicketSerializer

    def get_queryset(self):
        return (
            Ticket.objects.select_related('payment', 'payment__fare', 'trip', 'trip__route')
            .prefetch_related('scans')
            .filter(passenger=self.request.user)
            .order_by('-issued_at')
        )


# ============================================================================
# 2. GET SINGLE TICKET DETAIL (Owner or Admin)
#    Endpoint: GET /tickets/<uuid:pk>/
# ============================================================================
@extend_schema_view(
    get=extend_schema(
        tags=['Tickets'],
        summary='Get a single ticket with computed status',
    )
)
class TicketDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TicketSerializer

    def get_object(self):
        ticket = get_object_or_404(
            Ticket.objects.select_related('payment', 'payment__fare', 'trip', 'trip__route', 'passenger')
            .prefetch_related('scans'),
            pk=self.kwargs['pk'],
        )
        if ticket.passenger != self.request.user and getattr(self.request.user, 'role', None) != 'admin':
            raise PermissionDenied("You can only view your own tickets.")
        return ticket


# ============================================================================
# 3. GET SHORT-LIVED ROTATING SIGNED CODE (Owner)
#    Endpoint: GET /tickets/<uuid:pk>/code/
# ============================================================================
class TicketRotatingCodeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        tags=['Tickets'],
        summary='Get a fresh 12-hour signed code for casher scanning',
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, pk):
        ticket = get_object_or_404(
            Ticket.objects.select_related('payment', 'trip', 'passenger').prefetch_related('scans'),
            pk=pk,
        )
        if ticket.passenger != request.user and getattr(request.user, 'role', None) != 'admin':
            raise PermissionDenied("You can only generate a scan code for your own ticket.")

        ticket_amount = str(ticket.payment.amount) if hasattr(ticket, 'payment') and ticket.payment else "0.00"
        computed_status = ticket.get_computed_status()
        if computed_status in ('used', 'expired'):
            return Response(
                {
                    "ticket_id": ticket.id,
                    "code": ticket.code,
                    "passenger_count": ticket.passenger_count,
                    "quantity": ticket.passenger_count,
                    "amount": ticket_amount,
                    "token": None,
                    "rotating_token": None,
                    "valid_for_seconds": 0,
                    "expires_at": None,
                    "status": computed_status,
                    "detail": f"Ticket is {computed_status}.",
                },
                status=status.HTTP_200_OK,
            )

        data = generate_rotating_ticket_token(ticket)
        data["rotating_token"] = data.get("token")
        data["status"] = computed_status
        data["passenger_count"] = ticket.passenger_count
        data["quantity"] = ticket.passenger_count
        data["amount"] = ticket_amount
        return Response(data, status=status.HTTP_200_OK)


# ============================================================================
# 4. VALIDATE A TICKET (Casher / Admin)
#    Endpoint: POST /tickets/validate/
# ============================================================================
class TicketValidateView(APIView):
    permission_classes = [IsCasherRole]
    serializer_class = TicketValidateInputSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ticket_validate'

    @extend_schema(
        tags=['Tickets'],
        summary='Validate a passenger ticket code (Casher)',
        description=(
            'Verifies the rotating signed token, checks that the ticket belongs to the '
            'casher’s active trip, checks computed status, and logs a TicketScan row.'
        ),
        request=TicketValidateInputSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        serializer = TicketValidateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token'].strip()
        now = timezone.now()

        # Find the casher's currently active trip (if any)
        active_trip = Trip.objects.filter(
            casher=request.user, status=Trip.Status.ACTIVE
        ).first()

        # 1. Verify the rotating signed code (up to 12 hours) or short ticket code / UUID
        ticket_id = verify_rotating_ticket_token(raw_token)
        ticket = None
        if ticket_id:
            ticket = (
                Ticket.objects.select_related('payment', 'trip', 'passenger')
                .filter(id=ticket_id)
                .first()
            )
        if not ticket:
            ticket = (
                Ticket.objects.select_related('payment', 'trip', 'passenger')
                .filter(code__iexact=raw_token)
                .first()
            )
        if not ticket:
            try:
                import uuid
                ticket_uuid = uuid.UUID(raw_token)
                ticket = (
                    Ticket.objects.select_related('payment', 'trip', 'passenger')
                    .filter(id=ticket_uuid)
                    .first()
                )
            except (ValueError, AttributeError):
                pass

        # 2. Check in strict order:
        #    (a) Code is genuine & unexpired AND ticket exists -> else 'invalid'
        if not ticket:
            TicketScan.objects.create(
                ticket=None,
                casher=request.user,
                trip=active_trip,
                result=TicketScan.Result.INVALID,
                scanned_at=now,
            )
            return Response(
                {
                    "result": TicketScan.Result.INVALID,
                    "ticket_code": None,
                    "passenger_masked": None,
                    "passenger_count": None,
                    "quantity": None,
                    "amount": None,
                    "detail": "Invalid or expired rotating QR code.",
                },
                status=status.HTTP_200_OK,
            )

        ticket_amount = str(ticket.payment.amount) if hasattr(ticket, 'payment') and ticket.payment else "0.00"

        #    (b) Ticket belongs to the casher's current active trip -> else 'wrong_trip'
        if not active_trip or ticket.trip_id != active_trip.id:
            TicketScan.objects.create(
                ticket=ticket,
                casher=request.user,
                trip=active_trip,
                result=TicketScan.Result.WRONG_TRIP,
                scanned_at=now,
            )
            return Response(
                {
                    "result": TicketScan.Result.WRONG_TRIP,
                    "ticket_code": ticket.code,
                    "passenger_masked": mask_passenger_name(ticket.passenger),
                    "passenger_count": ticket.passenger_count,
                    "quantity": ticket.passenger_count,
                    "amount": ticket_amount,
                    "detail": "This ticket belongs to a different trip or bus.",
                },
                status=status.HTTP_200_OK,
            )

        #    (c) Check computed ticket status ('valid' vs 'expired' vs 'used')
        computed_status = ticket.get_computed_status()
        if computed_status == 'used':
            TicketScan.objects.create(
                ticket=ticket,
                casher=request.user,
                trip=active_trip,
                result=TicketScan.Result.INVALID,
                scanned_at=now,
            )
            return Response(
                {
                    "result": TicketScan.Result.INVALID,
                    "ticket_code": ticket.code,
                    "passenger_masked": mask_passenger_name(ticket.passenger),
                    "passenger_count": ticket.passenger_count,
                    "quantity": ticket.passenger_count,
                    "amount": ticket_amount,
                    "detail": "This ticket has already been used.",
                },
                status=status.HTTP_200_OK,
            )

        scan_result = (
            TicketScan.Result.VALID
            if computed_status == 'valid'
            else TicketScan.Result.EXPIRED
        )

        TicketScan.objects.create(
            ticket=ticket,
            casher=request.user,
            trip=active_trip,
            result=scan_result,
            scanned_at=now,
        )

        if scan_result == TicketScan.Result.VALID:
            try:
                record(
                    actor=request.user,
                    action=AuditLog.Action.TICKET_VALIDATED,
                    entity_type='ticket',
                    entity_id=ticket.id,
                    metadata={
                        'code': ticket.code,
                        'trip_id': str(active_trip.id) if active_trip else str(ticket.trip_id),
                        'passenger_id': str(ticket.passenger_id),
                        'passenger_count': ticket.passenger_count,
                    },
                )
            except Exception:
                pass

        return Response(
            {
                "result": scan_result,
                "ticket_code": ticket.code,
                "passenger_masked": mask_passenger_name(ticket.passenger),
                "passenger_count": ticket.passenger_count,
                "quantity": ticket.passenger_count,
                "amount": ticket_amount,
                "detail": "Ticket is valid." if scan_result == 'valid' else "Ticket has expired.",
            },
            status=status.HTTP_200_OK,
        )