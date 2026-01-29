"""
模型配置业务逻辑层

参考:
- constitution.md#1.1 服务层封装所有业务逻辑
- specs/003-model-config/spec.md FR-005~FR-014
"""
import logging
from typing import Any, Optional

from apps.models.models import ModelConfig
from apps.models.repositories import model_repo
from apps.users.crypto import sm4_decrypt, sm4_encrypt

logger = logging.getLogger(__name__)


def _mask_api_key(decrypted_key: str) -> str:
    """对解密后的 API Key 进行脱敏

    长度 > 8 时：前 4 位 + **** + 后 4 位
    长度 <= 8 时：全部脱敏为 ****
    参考: spec.md FR-009
    """
    if not decrypted_key or len(decrypted_key) <= 8:
        return "****"
    return f"{decrypted_key[:4]}****{decrypted_key[-4:]}"


def _model_to_dict(model: ModelConfig, masked_key: str) -> dict[str, Any]:
    """将模型实例转换为字典（含脱敏 API Key 和计算属性）"""
    return {
        "id": model.id,
        "type": model.type,
        "name": model.name,
        "url": model.url,
        "api_key": masked_key,
        "max_context_window": model.max_context_window,
        "max_input_tokens": model.max_input_tokens,
        "max_output_tokens": model.max_output_tokens,
        "temperature": model.temperature,
        "top_p": model.top_p,
        "frequency_penalty": model.frequency_penalty,
        "presence_penalty": model.presence_penalty,
        "embedding_dimensions": model.embedding_dimensions,
        "is_active": model.is_active,
        "effective_context_window": model.effective_context_window,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }


class ModelService:
    """模型配置业务服务"""

    @staticmethod
    def get_all_models() -> list[dict[str, Any]]:
        """获取所有模型配置（API Key 脱敏）

        参考: spec.md FR-009 脱敏处理
        """
        models = model_repo.get_all()
        result = []
        for model in models:
            try:
                decrypted_key = sm4_decrypt(model.api_key)
            except Exception:
                decrypted_key = ""
            masked_key = _mask_api_key(decrypted_key)
            result.append(_model_to_dict(model, masked_key))
        return result

    @staticmethod
    def get_model_by_id(model_id: int) -> Optional[dict[str, Any]]:
        """获取单个模型配置（API Key 脱敏）"""
        model = model_repo.get_by_id(model_id)
        if not model:
            return None
        try:
            decrypted_key = sm4_decrypt(model.api_key)
        except Exception:
            decrypted_key = ""
        masked_key = _mask_api_key(decrypted_key)
        return _model_to_dict(model, masked_key)

    @staticmethod
    def update_model(model_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        """更新模型配置

        处理 API Key 加密逻辑：
        - 若 api_key 包含 '****'，判定为脱敏值，保留原值（FR-005）
        - 否则使用 sm4_encrypt 加密新值（FR-008）

        NULL vs 0 语义由序列化器和此处直接传递保证（FR-006）

        Args:
            model_id: 模型 ID
            data: 更新数据

        Returns:
            更新后的模型数据（脱敏），或 None
        """
        model = model_repo.get_by_id(model_id)
        if not model:
            return None

        # 处理 api_key 加密逻辑
        api_key_value = data.get("api_key")
        if api_key_value is not None:
            if "****" in api_key_value:
                # 脱敏值，保留原值，从 data 中移除避免覆盖
                data.pop("api_key")
                logger.info(
                    f"Model {model_id}: api_key contains '****', keeping original value"
                )
            else:
                # 新密钥，加密存储
                data["api_key"] = sm4_encrypt(api_key_value)
                logger.info(f"Model {model_id}: api_key updated and encrypted")

        # 移除不可修改的字段（防御性编程）
        data.pop("type", None)
        data.pop("is_active", None)
        data.pop("id", None)
        data.pop("created_at", None)
        data.pop("updated_at", None)

        # 执行更新
        model = model_repo.update(model, **data)
        logger.info(f"Model config updated: id={model_id}, fields={list(data.keys())}")

        # 返回脱敏数据
        try:
            decrypted_key = sm4_decrypt(model.api_key)
        except Exception:
            decrypted_key = ""
        masked_key = _mask_api_key(decrypted_key)
        return _model_to_dict(model, masked_key)

    @staticmethod
    def get_active_model(model_type: str) -> Optional[dict[str, Any]]:
        """获取激活的模型配置（解密 API Key，供后端内部调用）

        参考: spec.md FR-014
        不受管理员权限限制，通过服务层内部调用。

        Args:
            model_type: 模型类型（language / embedding）

        Returns:
            模型配置字典（api_key 为解密明文），或 None
        """
        model = model_repo.get_active_by_type(model_type)
        if not model:
            logger.warning(f"No active model found for type: {model_type}")
            return None

        # 解密 API Key 为明文
        try:
            decrypted_key = sm4_decrypt(model.api_key)
        except Exception as e:
            logger.error(f"Failed to decrypt api_key for model {model.id}: {e}")
            decrypted_key = ""

        return _model_to_dict(model, decrypted_key)


model_service = ModelService()
