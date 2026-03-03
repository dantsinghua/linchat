from django.urls import path

from apps.media import views

urlpatterns = [
    path("upload/", views.upload_media, name="upload_media"),
    path("<str:uuid>/", views.get_media, name="get_media"),
]
