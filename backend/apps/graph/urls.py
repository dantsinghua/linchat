from django.urls import path

from apps.graph import views

urlpatterns = [
    path("cancel/", views.cancel_inference, name="cancel_inference"),
]
