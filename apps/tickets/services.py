from datetime import timedelta
import secrets
import string

from django.conf import settings
from django.core import signing
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.tickets.models import Ticket

TICKET_SIGNER_SALT = 'bus-ticket-rotating-qr-v1'
ROTATING_CODE_MAX_AGE_SECONDS = 60  # Rotating QR code expires after 60 seconds


def generate_short_code() -> str:
    """Generates a short human-readable code like 'A821'."""
    letter = secrets.choice(string.ascii_uppercase)
    digits = ''.join(secrets.choice(string.digits) for _ in range(3))
    return f"{letter}{digits}"


def issue_ticket_for_payment(payment) -> Ticket:
    """
    Creates a Ticket for a succeeded Payment inside the caller's atomic transaction.
    Idempotent: if the payment already has a ticket, returns it.
    """
    existing = Ticket.objects.filter(payment=payment).first()
    if existing:
        return existing

    now = timezone.now()
    max_hours = getattr(settings, 'TICKET_MAX_DURATION_HOURS', 3)
    expires_at = now + timedelta(hours=max_hours)

    passenger_count = getattr(payment, 'passenger_count', 1) or 1

    ticket = Ticket.objects.create(
        payment=payment,
        passenger=payment.passenger,
        trip=payment.trip,
        passenger_count=passenger_count,
        code=generate_short_code(),
        issued_at=now,
        expires_at=expires_at,
    )
    try:
        record(
            actor=payment.passenger,
            action=AuditLog.Action.TICKET_ISSUED,
            entity_type='ticket',
            entity_id=ticket.id,
            metadata={
                'code': ticket.code,
                'trip_id': str(payment.trip_id),
                'payment_id': str(payment.id),
                'passenger_count': ticket.passenger_count,
            },
        )
    except Exception:
        pass

    return ticket


def generate_rotating_ticket_token(ticket: Ticket) -> dict:
    """
    Signs the ticket UUID with a current timestamp so screenshots die after 60 seconds.
    """
    signer = signing.TimestampSigner(salt=TICKET_SIGNER_SALT)
    signed_token = signer.sign(str(ticket.id))
    passenger_count = getattr(ticket, 'passenger_count', 1) or 1
    amount_str = str(ticket.payment.amount) if hasattr(ticket, 'payment') and ticket.payment else None
    return {
        "ticket_id": ticket.id,
        "code": ticket.code,
        "passenger_count": passenger_count,
        "quantity": passenger_count,
        "amount": amount_str,
        "token": signed_token,
        "valid_for_seconds": ROTATING_CODE_MAX_AGE_SECONDS,
        "expires_at": timezone.now() + timedelta(seconds=ROTATING_CODE_MAX_AGE_SECONDS),
    }


def verify_rotating_ticket_token(token: str) -> str | None:
    """
    Verifies the signed rotating token and checks that it is not older than 60 seconds.
    Returns the ticket UUID string if valid, or None if tampered/expired.
    """
    signer = signing.TimestampSigner(salt=TICKET_SIGNER_SALT)
    try:
        ticket_id = signer.unsign(token, max_age=ROTATING_CODE_MAX_AGE_SECONDS)
        return ticket_id
    except (signing.BadSignature, signing.SignatureExpired):
        return None