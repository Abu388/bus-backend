from django.urls import path
from . import views

urlpatterns = [
    # Maintenance Endpoints
    path('buses/<uuid:pk>/maintenance/', views.BusMaintenanceView.as_view(), name='bus-maintenance'),
    path('buses/<uuid:pk>/resume/', views.BusResumeMaintenanceView.as_view(), name='bus-resume'),

    # Admin History & Reporting Endpoints
    path('admin/buses/<uuid:pk>/trips/', views.AdminBusTripsReportView.as_view(), name='admin-bus-trips'),
    path('admin/trips/', views.AdminTripsListView.as_view(), name='admin-trips-list'),
    path('admin/reports/summary/', views.AdminReportsSummaryView.as_view(), name='admin-reports-summary'),
    path('admin/fares/history/', views.AdminFareHistoryView.as_view(), name='admin-fares-history'),
    path('admin/audit/', views.AdminAuditLogListView.as_view(), name='admin-audit-list'),
]