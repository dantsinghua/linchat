"""
数据迁移：language→tool 重命名 + 新增 multimodal 记录

1. 将现有 type="language" 记录改为 type="tool"
2. 插入新 type="multimodal" 记录（MiniCPM-o）
"""
import logging
import os

from django.db import migrations

logger = logging.getLogger(__name__)


def rename_and_add_multimodal(apps, schema_editor):
    """language→tool 重命名 + 新增 multimodal 记录"""
    from apps.users.crypto import sm4_encrypt

    ModelConfig = apps.get_model("models", "ModelConfig")

    # 1. 将所有 language 记录改为 tool
    updated = ModelConfig.objects.filter(type="language").update(type="tool")
    logger.info("Renamed %d language model(s) to tool", updated)

    # 2. 新增 multimodal 记录
    mm_model_name = os.getenv("LLM_MULTIMODAL_MODEL", "minicpm-o")
    mm_url = os.getenv("LLM_GATEWAY_URL", "http://127.0.0.1:8100")
    mm_api_key_raw = os.getenv("LLM_GATEWAY_API_KEY", "")

    if mm_api_key_raw:
        mm_api_key = sm4_encrypt(mm_api_key_raw)
    else:
        mm_api_key = sm4_encrypt("placeholder-key!")
        logger.warning(
            "Multimodal model seed: LLM_GATEWAY_API_KEY not found, "
            "using placeholder. Please update via model config page."
        )

    ModelConfig.objects.create(
        type="multimodal",
        name=mm_model_name,
        url=mm_url,
        api_key=mm_api_key,
        max_context_window=4096,
        max_input_tokens=2048,
        max_output_tokens=1024,
        is_active=True,
    )
    logger.info("Created multimodal model config: %s", mm_model_name)


def reverse_migration(apps, schema_editor):
    """回滚：删除 multimodal 记录 + tool→language 重命名"""
    ModelConfig = apps.get_model("models", "ModelConfig")
    ModelConfig.objects.filter(type="multimodal").delete()
    ModelConfig.objects.filter(type="tool").update(type="language")


class Migration(migrations.Migration):

    dependencies = [
        ("models", "0002_seed_model_configs"),
    ]

    operations = [
        migrations.RunPython(rename_and_add_multimodal, reverse_migration),
    ]
