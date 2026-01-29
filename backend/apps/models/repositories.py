"""
模型配置数据访问层

参考: constitution.md#1.1 数据仓库层封装数据访问
"""
import logging
from typing import Optional

from apps.models.models import ModelConfig

logger = logging.getLogger(__name__)


class ModelRepository:
    """模型配置数据仓库"""

    @staticmethod
    def get_all() -> list[ModelConfig]:
        """获取所有模型配置"""
        return list(ModelConfig.objects.all().order_by("id"))

    @staticmethod
    def get_by_id(model_id: int) -> Optional[ModelConfig]:
        """根据 ID 获取模型配置"""
        try:
            return ModelConfig.objects.get(pk=model_id)
        except ModelConfig.DoesNotExist:
            return None

    @staticmethod
    def get_active_by_type(model_type: str) -> Optional[ModelConfig]:
        """根据类型获取激活的模型配置

        参考: spec.md FR-014
        """
        try:
            return ModelConfig.objects.get(type=model_type, is_active=True)
        except ModelConfig.DoesNotExist:
            return None

    @staticmethod
    def update(model: ModelConfig, **kwargs) -> ModelConfig:
        """更新模型配置

        Args:
            model: 模型实例
            **kwargs: 要更新的字段
        """
        for key, value in kwargs.items():
            setattr(model, key, value)
        model.save()
        return model


model_repo = ModelRepository()
