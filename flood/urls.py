from django.urls import path

from . import views

app_name = "flood"

urlpatterns = [
    path("api/flood/susceptibility/", views.api_flood_susceptibility,
         name="api_flood_susceptibility"),
]
