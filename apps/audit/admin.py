from django.contrib import admin

# Register your models here.

from django.contrib import admin
from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'actor', 'actor_kind', 'entity_type', 'entity_id')
    list_filter = ('action', 'actor_kind', 'entity_type')
    search_fields = ('entity_id', 'action')
    readonly_fields = (
        'id',
        'actor',
        'actor_kind',
        'action',
        'entity_type',
        'entity_id',
        'metadata',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False