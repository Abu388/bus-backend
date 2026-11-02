from django.urls import path
from apps.core.views import DemoPaymentSimulatorView, HealthCheckView

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('payments/simulate/', DemoPaymentSimulatorView.as_view(), name='payment-simulate'),
]