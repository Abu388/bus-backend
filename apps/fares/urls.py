from django.urls import path

from . import views

urlpatterns = [
    path('', views.FareListCreateView.as_view(), name='fare-list-create'),
]