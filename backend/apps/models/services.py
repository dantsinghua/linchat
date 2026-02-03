"""模型配置业务逻辑层"""

import logging
from typing import Any, Optional

from apps.models.models import ModelConfig
from apps.models.repositories import model_repo
from apps.users.crypto import sm4_decrypt, sm4_encrypt

logger = logging.getLogger(__name__)


def _mask_api_key(decrypted_key: str) -> str:
    if not decrypted_key or len(decrypted_key) <= 8:
        return "****"
    return f"{decrypted_key[:4]}****{decrypted_key[-4:]}"


def _model_to_dict(model: ModelConfig, api_key_value: str) -> dict[str, Any]:
    return {
        "id": model.id, "type": model.type, "name": model.name,
        "url": model.url, "api_key": api_key_value,
        "max_context_window": model.max_context_window,
        "max_input_tokens": model.max_input_tokens,
        "max_output_tokens": model.max_output_tokens,
        "temperature": model.temperature, "top_p": model.top_p,
        "frequency_penalty": model.frequency_penalty,
        "presence_penalty": model.presence_penalty,
        "embedding_dimensions": model.embedding_dimensions,
        "is_active": model.is_active,
        "effective_context_window": model.effective_context_window,
        "created_at": model.created_at, "updated_at": model.updated_at,
    }


def _to_dict_with_key(model: ModelConfig, decrypt: bool = False) -> dict[str, Any]:
    """将模型转为字典，decrypt=True 返回明文，False 返回脱敏"""
    try:
        decrypted = sm4_decrypt(model.api_key)
    except Exception:
        decrypted = ""
    key = decrypted if decrypt else _mask_api_key(decrypted)
    return _model_to_dict(model, key)


class ModelService:

    @staticmethod
    def get_all_models() -> list[dict[str, Any]]:
        return [_to_dict_with_key(m) for m in model_repo.get_all()]

    @staticmethod
    def get_model_by_id(model_id: int) -> Optional[dict[str, Any]]:
        model = model_repo.get_by_id(model_id)
        return _to_dict_with_key(model) if model else None

    @staticmethod
    def update_model(model_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        model = model_repo.get_by_id(model_id)
        if not model:
            return None

        api_key_value = data.get("api_key")
        if api_key_value is not None:
            if "****" in api_key_value:
                data.pop("api_key")
            else:
                data["api_key"] = sm4_encrypt(api_key_value)

        for key in ("type", "is_active", "id", "created_at", "updated_at"):
            data.pop(key, None)

        model = model_repo.update(model, **data)
        logger.info(f"Model config updated: id={model_id}, fields={list(data.keys())}")
        return _to_dict_with_key(model)

    @staticmethod
    def get_active_model(model_type: str) -> Optional[dict[str, Any]]:
        model = model_repo.get_active_by_type(model_type)
        if not model:
            logger.warning(f"No active model found for type: {model_type}")
            return None
        return _to_dict_with_key(model, decrypt=True)


model_service = ModelService()
