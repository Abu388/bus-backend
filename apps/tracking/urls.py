from django.urls import path
from . import views

urlpatterns = [
    # GET /buses/live/ (All live buses for map)
    path('buses/live/', views.LiveBusesListView.as_view(), name='buses-live'),

    # GET /buses/search/?number=124 (Search live buses by public route number)
    path('buses/search/', views.BusSearchByNumberView.as_view(), name='buses-search'),

    # GET & POST /buses/<id>/location/ (Get last location or Post new GPS ping)
    path('buses/<uuid:pk>/location/', views.BusLocationView.as_view(), name='bus-location'),

    # GET /buses/<id>/eta/ (Get next fermata and ETAs for upcoming stops)
    path('buses/<uuid:pk>/eta/', views.BusETAView.as_view(), name='bus-eta'),
]