
from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role == 'admin'

class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS: #GET, HEAD, or OPTIONS methonds are safe methods, so we check if the request method is in SAFE_METHODS. If it is, we allow the request to proceed. If it is not, we check if the user is authenticated and has the 'admin' role. If both conditions are met, we allow the request to proceed. Otherwise, we deny the request.
            return True
        return request.user and request.user.is_authenticated and request.user.role == 'admin' # we use this if the request for POST, PUT, PATCH, or DELETE


class IsCasherRole(BasePermission):
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated and (request.user.role == 'admin' or request.user.role == 'casher'):  
            if request.method in SAFE_METHODS  :
                return True
        return request.user and request.user.is_authenticated and request.user.role == 'casher'

