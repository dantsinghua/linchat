"""
种子数据迁移：预置 language 和 embedding 模型记录

参考:
- specs/003-model-config/data-model.md#5 初始数据种子
- specs/003-model-config/spec.md FR-002
"""
import logging
import os

from django.db import migrations

logger = logging.getLogger(__name__)


def seed_model_configs(apps, schema_editor):
    """预置 2 条模型记录：language + embedding"""
    from apps.users.crypto import sm4_encrypt

    ModelConfig = apps.get_model("models", "ModelConfig")

    # ===== Language 模型：从环境变量读取初始值 =====
    llm_api_base = os.getenv("LLM_API_BASE", "")
    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_model_name = os.getenv("LLM_MODEL_NAME", "")

    if llm_api_base and llm_api_key and llm_model_name:
        language_name = llm_model_name
        language_url = llm_api_base
        language_api_key = sm4_encrypt(llm_api_key)
        logger.info("Language model seed: using environment variables")
    else:
        # 环境变量缺失，使用合规占位值
        language_name = "language-placeholder"
        language_url = "https://api.placeholder.com/v1"
        language_api_key = sm4_encrypt("placeholder-key!")
        logger.warning(
            "Language model seed: LLM_API_BASE/LLM_API_KEY/LLM_MODEL_NAME "
            "not found in environment, using placeholder values. "
            "Please update via the model config page."
        )

    ModelConfig.objects.create(
        type="language",
        name=language_name,
        url=language_url,
        api_key=language_api_key,
        max_context_window=65536,
        max_input_tokens=32768,
        max_output_tokens=8192,
        is_active=True,
    )

    # ===== Embedding 模型：使用合规占位值 =====
    ModelConfig.objects.create(
        type="embedding",
        name="text-embedding-placeholder",
        url="https://api.placeholder.com/v1",
        api_key=sm4_encrypt("placeholder-key!"),
        max_context_window=8192,
        max_input_tokens=8192,
        max_output_tokens=1,
        embedding_dimensions=1536,
        is_active=True,
    )

    logger.info("Model config seed data created: 2 records (language + embedding)")


def reverse_seed(apps, schema_editor):
    """回滚：删除种子数据"""
    ModelConfig = apps.get_model("models", "ModelConfig")
    ModelConfig.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("models", "0001_create_model_config"),
    ]

    operations = [
        migrations.RunPython(seed_model_configs, reverse_seed),
    ]
