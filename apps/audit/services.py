from apps.audit.models import AuditLog

SENSITIVE_KEYS = {'password', 'token', 'secret', 'email', 'phone_number', 'phone'}


def _sanitize_metadata(data: dict) -> dict:
    """Strips sensitive keys (passwords, tokens, full phone/email) before storing."""
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for key, value in data.items():
        if str(key).lower() in SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _sanitize_metadata(value)
        else:
            cleaned[key] = value
    return cleaned


def record(
    *,
    actor=None,
    actor_kind: str = AuditLog.ActorKind.USER,
    action: str,
    entity_type: str,
    entity_id,
    metadata: dict | None = None,
) -> AuditLog:
    """
    Writes an append-only AuditLog entry inside the caller's database transaction.
    """
    if actor is not None and getattr(actor, 'is_anonymous', False):
        actor = None

    return AuditLog.objects.create(
        actor=actor,
        actor_kind=actor_kind,
        action=action,
        entity_type=str(entity_type),
        entity_id=str(entity_id),
        metadata=_sanitize_metadata(metadata or {}),
    )