from django.urls import path
from . import views

urlpatterns = [
    path('buses/', views.PhysicalBusListCreateView.as_view(), name='bus-list-create'),
    path('buses/<uuid:pk>/', views.PhysicalBusDetailView.as_view(), name='bus-detail'),
    path('maintenance/', views.MaintenanceEventListCreateView.as_view(), name='maintenance-list-create'),
    path('maintenance/<uuid:pk>/', views.MaintenanceEventDetailView.as_view(), name='maintenance-detail'),
]