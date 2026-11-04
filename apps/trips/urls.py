from django.urls import path
from . import views

urlpatterns = [
    # POST /trips/ (Start trip) and GET /trips/ (Admin trip history)
    path('trips/', views.TripListCreateView.as_view(), name='trip-list-create'),

    # GET /trips/active/ (Casher's current active trip)
    path('trips/active/', views.ActiveTripView.as_view(), name='trip-active'),

    # POST /trips/<id>/end/ (End an active trip)
    path('trips/<uuid:pk>/end/', views.EndTripView.as_view(), name='trip-end'),

    # POST /trips/<id>/reverse/ (End current trip + Start opposite direction trip)
    path('trips/<uuid:pk>/reverse/', views.ReverseTripView.as_view(), name='trip-reverse'),

    # GET /trips/<id>/qr/ (Get current QR token and QR image URL)
    path('trips/<uuid:pk>/qr/', views.TripQRView.as_view(), name='trip-qr'),

    # POST /trip-qr/scan/ (Passenger scans QR token to get trip & fare summary)
    path('trip-qr/scan/', views.TripQRScanView.as_view(), name='trip-qr-scan'),
]