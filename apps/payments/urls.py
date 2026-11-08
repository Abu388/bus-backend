from django.urls import path
from . import views

urlpatterns = [
    # POST /payments/ and POST /payments/initiate/ (Passenger starts a payment from a QR token)
    path('payments/', views.InitiatePaymentView.as_view(), name='payment-create'),
    path('payments/initiate/', views.InitiatePaymentView.as_view(), name='payment-initiate'),

    # GET /payments/<id>/ (Passenger or Admin checks payment status)
    path('payments/<uuid:pk>/', views.PaymentDetailView.as_view(), name='payment-detail'),

    # POST /payments/webhook/<provider>/ (Provider sends signed webhook notification)
    path('payments/webhook/<str:provider>/', views.PaymentWebhookView.as_view(), name='payment-webhook'),

    # GET /trips/<id>/payments/ (Casher or Admin views succeeded payments & total collected)
    path('trips/<uuid:pk>/payments/', views.TripPaymentsCasherView.as_view(), name='trip-payments'),
]