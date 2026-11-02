from django.urls import path
from . import views

urlpatterns = [
    # Fermatas endpoints
    path('fermatas/', views.FermataListCreateView.as_view(), name='fermata-list-create'),
    path('fermatas/<uuid:pk>/', views.FermataDetailView.as_view(), name='fermata-detail'),

    # Routes endpoints
    path('routes/', views.RouteListCreateView.as_view(), name='route-list-create'),
    path('routes/<uuid:pk>/', views.RouteDetailView.as_view(), name='route-detail'),

    # Route Stops endpoints
    path('stops/', views.RouteStopListCreateView.as_view(), name='route-stop-list-create'),
    path('stops/<uuid:pk>/', views.RouteStopDetailView.as_view(), name='route-stop-detail'),

    # Route Directions endpoints
    path('directions/', views.RouteDirectionListCreateView.as_view(), name='route-direction-list-create'),
    path('directions/<uuid:pk>/', views.RouteDirectionDetailView.as_view(), name='route-direction-detail'),
]