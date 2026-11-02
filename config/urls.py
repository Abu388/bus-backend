"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.core.views import HealthCheckView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Top-level /health/ check (for Render) + /api/health/ via apps.core.urls
    path('health/', HealthCheckView.as_view(), name='root-health-check'),

    # OpenAPI 3 Schema & Swagger UI
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),

    # Core (Health & Demo Payment Simulator)
    path('api/', include('apps.core.urls')),

    # Domain App APIs
    path('api/', include('apps.accounts.urls')),
    path('api/', include('apps.fleet.urls')),
    path('api/', include('apps.routes.urls')),
    path('api/', include('apps.fares.urls')),
    path('api/', include('apps.trips.urls')),
    path('api/', include('apps.tracking.urls')),
    path('api/', include('apps.payments.urls')),
    path('api/', include('apps.tickets.urls')),
    path('api/', include('apps.audit.urls')),
]