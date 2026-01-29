"""
模型配置 API 视图

参考:
- constitution.md#1.1 视图层仅处理 HTTP 请求响应
- specs/003-model-config/spec.md FR-003, FR-004, FR-013, FR-016
- contracts/api.yaml
"""
import logging

from rest_framework.views import APIView

from apps.common.responses import ApiResponse
from apps.models.permissions import IsAdminUser
from apps.models.serializers import ModelResponseSerializer, ModelUpdateSerializer
from apps.models.services import model_service

logger = logging.getLogger(__name__)


class ModelListView(APIView):
    """模型配置列表视图

    GET /api/v1/models/ — 获取所有模型配置
    仅定义 GET 方法，DRF 自动对未定义方法返回 405（FR-013）。
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        """获取所有模型配置列表"""
        models = model_service.get_all_models()
        serializer = ModelResponseSerializer(models, many=True)
        return ApiResponse.success(data=serializer.data)


class ModelDetailView(APIView):
    """模型配置详情视图

    GET /api/v1/models/<id>/ — 获取单个模型配置
    PUT /api/v1/models/<id>/ — 更新模型配置
    仅定义 GET + PUT 方法，DRF 自动对其他方法返回 405（FR-013）。
    """

    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        """获取单个模型配置"""
        model_data = model_service.get_model_by_id(pk)
        if not model_data:
            return ApiResponse.not_found(message="模型配置不存在")
        serializer = ModelResponseSerializer(model_data)
        return ApiResponse.success(data=serializer.data)

    def put(self, request, pk):
        """更新模型配置

        参考: spec.md FR-004, FR-005, FR-006, FR-012
        """
        # 获取模型以确认存在并获取 type 用于跨字段校验
        existing = model_service.get_model_by_id(pk)
        if not existing:
            return ApiResponse.not_found(message="模型配置不存在")

        serializer = ModelUpdateSerializer(
            data=request.data,
            context={"model_type": existing["type"]},
        )
        if not serializer.is_valid():
            return ApiResponse.validation_error(
                message="参数校验失败",
                errors=serializer.errors,
            )

        updated = model_service.update_model(pk, serializer.validated_data)
        if not updated:
            return ApiResponse.not_found(message="模型配置不存在")

        response_serializer = ModelResponseSerializer(updated)
        logger.info(
            f"Model config updated by user {getattr(request, 'username', 'unknown')}: "
            f"model_id={pk}"
        )
        return ApiResponse.success(data=response_serializer.data)
