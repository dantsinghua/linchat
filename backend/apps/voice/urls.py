"""Voice REST URL 路由

参考:
- specs/009-voice-interaction/data-model.md API 端点
"""

from django.urls import path

from apps.voice.views import (
    DeviceDeleteView,
    DeviceListCreateView,
    SpeakerDeleteView,
    SpeakerListCreateView,
    VoiceSettingsView,
)

urlpatterns = [
    # 声纹管理（T034）
    path("speakers/", SpeakerListCreateView.as_view(), name="voice-speakers"),
    path("speakers/delete/", SpeakerDeleteView.as_view(), name="voice-speaker-delete"),

    # 设备管理（T035）
    path("devices/", DeviceListCreateView.as_view(), name="voice-devices"),
    path("devices/<str:device_uuid>/", DeviceDeleteView.as_view(), name="voice-device-delete"),

    # 语音设置（T044）
    path("settings/", VoiceSettingsView.as_view(), name="voice-settings"),
]
