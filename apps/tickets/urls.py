from django.urls import path
from . import views

urlpatterns = [
    # GET /tickets/ (Passenger ticket history)
    path('tickets/', views.TicketListView.as_view(), name='ticket-list'),

    # POST /tickets/validate/ (Cashier validates a ticket token)
    path('tickets/validate/', views.TicketValidateView.as_view(), name='ticket-validate'),

    # GET /tickets/<id>/ (Single ticket detail)
    path('tickets/<uuid:pk>/', views.TicketDetailView.as_view(), name='ticket-detail'),

    # GET /tickets/<id>/code/ (Fresh 60s rotating signed code)
    path('tickets/<uuid:pk>/code/', views.TicketRotatingCodeView.as_view(), name='ticket-rotating-code'),
]