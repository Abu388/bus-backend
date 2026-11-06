from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == 'admin'
        )


class IsAdminOrCasherReadOnly(BasePermission):
    """Admin can GET/POST/PUT/PATCH/DELETE; Casher can GET/HEAD/OPTIONS."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.role == 'admin':
            return True
        if request.user.role == 'casher' and request.method in SAFE_METHODS:
            return True
        return False


class IsCasherRole(BasePermission):
    """Allows Casher and Admin to read and write maintenance/casher endpoints."""
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in ('admin', 'casher')
        )