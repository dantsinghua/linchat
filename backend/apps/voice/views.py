import logging

from asgiref.sync import async_to_sync
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.common.responses import ApiResponse
from apps.voice.serializers import (
    CreateDeviceSerializer,
    CreateSpeakerSerializer,
    RegisteredDeviceSerializer,
    SpeakerProfileSerializer,
    VoiceSettingsSerializer,
    VoiceSettingsUpdateSerializer,
)
from apps.voice.services.device_service import device_service
from apps.voice.services.speaker_service import SpeakerRegistrationError, speaker_service
from apps.voice.services.voice_settings_service import voice_settings_service

logger = logging.getLogger(__name__)


class SpeakerRegistrationThrottle(UserRateThrottle):
    scope = "speaker_registration"
    rate = "5/hour"


class SpeakerListCreateView(APIView):
    parser_classes = [MultiPartParser]

    def get_throttles(self):
        if self.request.method == "POST":
            return [SpeakerRegistrationThrottle()]
        return super().get_throttles()

    def get(self, request: Request) -> Response:
        user_id: int = request.user_id
        speaker_info = async_to_sync(speaker_service.list_speakers)(user_id)
        logger.info("Speaker list queried: user_id=%s, has_speaker=%s", user_id, speaker_info is not None)
        return ApiResponse.success(data=speaker_info)

    def post(self, request: Request) -> Response:
        serializer = CreateSpeakerSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse.validation_error(message="参数校验失败", errors=serializer.errors)
        user_id: int = request.user_id
        name: str = serializer.validated_data["name"]
        audio_data: bytes = request.FILES["audio"].read()
        try:
            result = async_to_sync(speaker_service.register_speaker)(
                user_id=user_id, name=name, audio_data=audio_data,
            )
        except SpeakerRegistrationError as e:
            logger.warning("Speaker registration failed: user_id=%s, error=%s", user_id, e)
            return ApiResponse.error(message=str(e), code="SPEAKER_REGISTRATION_ERROR")
        logger.info("Speaker registered: user_id=%s, name=%s", user_id, name)
        return ApiResponse.created(data=result)


class SpeakerDeleteView(APIView):
    def delete(self, request: Request) -> Response:
        user_id: int = request.user_id
        deleted = async_to_sync(speaker_service.delete_speaker)(user_id)
        if not deleted:
            return ApiResponse.not_found(message="未找到声纹")
        logger.info("Speaker deleted via API: user_id=%s", user_id)
        return ApiResponse.success(message="声纹已删除")


class DeviceListCreateView(APIView):
    def get(self, request: Request) -> Response:
        user_id: int = request.user_id
        devices = async_to_sync(device_service.list_devices)(user_id)
        logger.info("Device list queried: user_id=%s, count=%d", user_id, len(devices))
        return ApiResponse.success(data=devices)

    def post(self, request: Request) -> Response:
        serializer = CreateDeviceSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse.validation_error(message="参数校验失败", errors=serializer.errors)
        user_id: int = request.user_id
        name: str = serializer.validated_data["name"]
        result = async_to_sync(device_service.register_device)(user_id=user_id, name=name)
        logger.info("Device registered via API: user_id=%s, device_uuid=%s", user_id, result["device_uuid"])
        return ApiResponse.created(data=result)


class DeviceDeleteView(APIView):
    def delete(self, request: Request, device_uuid: str) -> Response:
        user_id: int = request.user_id
        revoked = async_to_sync(device_service.revoke_device)(user_id=user_id, device_uuid=device_uuid)
        if not revoked:
            return ApiResponse.not_found(message="设备不存在或已停用")
        logger.info("Device revoked via API: user_id=%s, device_uuid=%s", user_id, device_uuid)
        return ApiResponse.success(message="设备已停用")


class VoiceSettingsView(APIView):
    def get(self, request: Request) -> Response:
        user_id: int = request.user_id
        voice_settings = async_to_sync(voice_settings_service.get_settings)(user_id)
        serializer = VoiceSettingsSerializer(voice_settings)
        return ApiResponse.success(data=serializer.data)

    def put(self, request: Request) -> Response:
        serializer = VoiceSettingsUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse.validation_error(message="参数校验失败", errors=serializer.errors)
        user_id: int = request.user_id
        update_data = {k: v for k, v in serializer.validated_data.items() if v is not None}
        if not update_data:
            return ApiResponse.error(message="未提供需要更新的字段")
        voice_settings = async_to_sync(voice_settings_service.update_settings)(user_id, **update_data)
        result_serializer = VoiceSettingsSerializer(voice_settings)
        return ApiResponse.success(data=result_serializer.data, message="语音设置已更新")
