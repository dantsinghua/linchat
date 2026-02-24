"""语音模块 REST 视图

参考:
- specs/009-voice-interaction/data-model.md API 端点
- specs/009-voice-interaction/contracts/api.yaml

视图层仅处理 HTTP 请求响应，业务逻辑委托 speaker_service / device_service。
"""

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


# T055a: 声纹注册特殊频率限制（5次/时，防止滥用 llmgateway 资源）
class SpeakerRegistrationThrottle(UserRateThrottle):
    scope = "speaker_registration"
    rate = "5/hour"


# ===========================================================================
# T034: 声纹管理视图
# ===========================================================================


class SpeakerListCreateView(APIView):
    """声纹列表查询 / 声纹注册

    GET  /api/v1/voice/speakers/ -- 查询当前用户的声纹信息
    POST /api/v1/voice/speakers/ -- 注册新声纹（multipart/form-data 接收音频）
    """

    parser_classes = [MultiPartParser]

    def get_throttles(self):
        """T055a: POST 请求使用声纹注册专用限流"""
        if self.request.method == "POST":
            return [SpeakerRegistrationThrottle()]
        return super().get_throttles()

    def get(self, request: Request) -> Response:
        """查询当前用户的声纹信息"""
        user_id: int = request.user_id

        speaker_info = async_to_sync(speaker_service.list_speakers)(user_id)

        logger.info("Speaker list queried: user_id=%s, has_speaker=%s", user_id, speaker_info is not None)

        return ApiResponse.success(data=speaker_info)

    def post(self, request: Request) -> Response:
        """注册新声纹

        接收 multipart/form-data 请求，包含 name 和 audio 文件。
        如果用户已有声纹，服务层会先删除旧声纹再创建新的。
        """
        serializer = CreateSpeakerSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse.validation_error(
                message="参数校验失败",
                errors=serializer.errors,
            )

        user_id: int = request.user_id
        name: str = serializer.validated_data["name"]
        audio_data: bytes = request.FILES["audio"].read()

        try:
            result = async_to_sync(speaker_service.register_speaker)(
                user_id=user_id,
                name=name,
                audio_data=audio_data,
            )
        except SpeakerRegistrationError as e:
            logger.warning(
                "Speaker registration failed: user_id=%s, error=%s",
                user_id,
                e,
            )
            return ApiResponse.error(
                message=str(e),
                code="SPEAKER_REGISTRATION_ERROR",
            )

        logger.info("Speaker registered: user_id=%s, name=%s", user_id, name)

        return ApiResponse.created(data=result)


class SpeakerDeleteView(APIView):
    """声纹删除

    DELETE /api/v1/voice/speakers/delete/ -- 删除当前用户的声纹
    """

    def delete(self, request: Request) -> Response:
        """删除当前用户的声纹"""
        user_id: int = request.user_id

        deleted = async_to_sync(speaker_service.delete_speaker)(user_id)

        if not deleted:
            return ApiResponse.not_found(message="未找到声纹")

        logger.info("Speaker deleted via API: user_id=%s", user_id)

        return ApiResponse.success(message="声纹已删除")


# ===========================================================================
# T035: 设备管理视图
# ===========================================================================


class DeviceListCreateView(APIView):
    """设备列表查询 / 设备注册

    GET  /api/v1/voice/devices/ -- 列出当前用户的所有注册设备
    POST /api/v1/voice/devices/ -- 注册新设备
    """

    def get(self, request: Request) -> Response:
        """列出当前用户的所有注册设备"""
        user_id: int = request.user_id

        devices = async_to_sync(device_service.list_devices)(user_id)

        logger.info("Device list queried: user_id=%s, count=%d", user_id, len(devices))

        return ApiResponse.success(data=devices)

    def post(self, request: Request) -> Response:
        """注册新设备

        返回的 api_token 仅在注册时可见一次，后续无法再次获取。
        """
        serializer = CreateDeviceSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse.validation_error(
                message="参数校验失败",
                errors=serializer.errors,
            )

        user_id: int = request.user_id
        name: str = serializer.validated_data["name"]

        result = async_to_sync(device_service.register_device)(
            user_id=user_id,
            name=name,
        )

        logger.info(
            "Device registered via API: user_id=%s, device_uuid=%s",
            user_id,
            result["device_uuid"],
        )

        return ApiResponse.created(data=result)


class DeviceDeleteView(APIView):
    """设备停用

    DELETE /api/v1/voice/devices/<device_uuid>/ -- 停用指定设备（软删除）
    """

    def delete(self, request: Request, device_uuid: str) -> Response:
        """停用指定设备（软删除，设置 is_active=False）"""
        user_id: int = request.user_id

        revoked = async_to_sync(device_service.revoke_device)(
            user_id=user_id,
            device_uuid=device_uuid,
        )

        if not revoked:
            return ApiResponse.not_found(message="设备不存在或已停用")

        logger.info(
            "Device revoked via API: user_id=%s, device_uuid=%s",
            user_id,
            device_uuid,
        )

        return ApiResponse.success(message="设备已停用")


# ===========================================================================
# T044: 语音设置视图
# ===========================================================================


class VoiceSettingsView(APIView):
    """语音设置查询 / 更新

    GET  /api/v1/voice/settings/ -- 获取当前用户的语音设置（不存在则自动创建默认值）
    PUT  /api/v1/voice/settings/ -- 更新语音设置（支持部分更新）
    """

    def get(self, request: Request) -> Response:
        """获取语音设置"""
        user_id: int = request.user_id

        voice_settings = async_to_sync(
            voice_settings_service.get_settings
        )(user_id)

        serializer = VoiceSettingsSerializer(voice_settings)
        return ApiResponse.success(data=serializer.data)

    def put(self, request: Request) -> Response:
        """更新语音设置"""
        serializer = VoiceSettingsUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return ApiResponse.validation_error(
                message="参数校验失败",
                errors=serializer.errors,
            )

        user_id: int = request.user_id
        update_data = {
            k: v
            for k, v in serializer.validated_data.items()
            if v is not None
        }

        if not update_data:
            return ApiResponse.error(message="未提供需要更新的字段")

        voice_settings = async_to_sync(
            voice_settings_service.update_settings
        )(user_id, **update_data)

        result_serializer = VoiceSettingsSerializer(voice_settings)

        return ApiResponse.success(
            data=result_serializer.data, message="语音设置已更新"
        )
