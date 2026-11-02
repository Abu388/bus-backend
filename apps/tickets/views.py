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
            Ticket.objects.select_related('payment', 'trip', 'trip__route')
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
            Ticket.objects.select_related('payment', 'trip', 'trip__route', 'passenger'),
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
        summary='Get a fresh 60-second rotating signed code for cashier scanning',
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request, pk):
        ticket = get_object_or_404(
            Ticket.objects.select_related('trip', 'passenger'),
            pk=pk,
        )
        if ticket.passenger != request.user:
            raise PermissionDenied("You can only generate a scan code for your own ticket.")

        data = generate_rotating_ticket_token(ticket)
        data["status"] = ticket.get_computed_status()
        return Response(data, status=status.HTTP_200_OK)


# ============================================================================
# 4. VALIDATE A TICKET (Cashier / Admin)
#    Endpoint: POST /tickets/validate/
# ============================================================================
class TicketValidateView(APIView):
    permission_classes = [IsCasherRole]
    serializer_class = TicketValidateInputSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ticket_validate'

    @extend_schema(
        tags=['Tickets'],
        summary='Validate a passenger ticket code (Cashier)',
        description=(
            'Verifies the rotating signed token, checks that the ticket belongs to the '
            'cashier’s active trip, checks computed status, and logs a TicketScan row.'
        ),
        request=TicketValidateInputSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        serializer = TicketValidateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token'].strip()
        now = timezone.now()

        # Find the cashier's currently active trip (if any)
        active_trip = Trip.objects.filter(
            casher=request.user, status=Trip.Status.ACTIVE
        ).first()

        # 1. Verify the rotating signed code
        ticket_id = verify_rotating_ticket_token(raw_token)
        ticket = None
        if ticket_id:
            ticket = (
                Ticket.objects.select_related('trip', 'passenger')
                .filter(id=ticket_id)
                .first()
            )

        # 2. Check in strict order:
        #    (a) Code is genuine & unexpired AND ticket exists -> else 'invalid'
        if not ticket:
            TicketScan.objects.create(
                ticket=None,
                cashier=request.user,
                trip=active_trip,
                result=TicketScan.Result.INVALID,
                scanned_at=now,
            )
            return Response(
                {
                    "result": TicketScan.Result.INVALID,
                    "ticket_code": None,
                    "passenger_masked": None,
                    "detail": "Invalid or expired rotating QR code.",
                },
                status=status.HTTP_200_OK,
            )

        #    (b) Ticket belongs to the cashier's current active trip -> else 'wrong_trip'
        if not active_trip or ticket.trip_id != active_trip.id:
            TicketScan.objects.create(
                ticket=ticket,
                cashier=request.user,
                trip=active_trip,
                result=TicketScan.Result.WRONG_TRIP,
                scanned_at=now,
            )
            return Response(
                {
                    "result": TicketScan.Result.WRONG_TRIP,
                    "ticket_code": ticket.code,
                    "passenger_masked": mask_passenger_name(ticket.passenger),
                    "detail": "This ticket belongs to a different trip or bus.",
                },
                status=status.HTTP_200_OK,
            )

        #    (c) Check computed ticket status ('valid' vs 'expired')
        computed_status = ticket.get_computed_status()
        scan_result = (
            TicketScan.Result.VALID
            if computed_status == 'valid'
            else TicketScan.Result.EXPIRED
        )

        TicketScan.objects.create(
            ticket=ticket,
            cashier=request.user,
            trip=active_trip,
            result=scan_result,
            scanned_at=now,
        )

        return Response(
            {
                "result": scan_result,
                "ticket_code": ticket.code,
                "passenger_masked": mask_passenger_name(ticket.passenger),
                "detail": "Ticket is valid." if scan_result == 'valid' else "Ticket has expired.",
            },
            status=status.HTTP_200_OK,
        )