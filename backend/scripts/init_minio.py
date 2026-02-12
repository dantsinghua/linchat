#!/usr/bin/env python
"""
MinIO Bucket 初始化脚本

创建多模态功能所需的 MinIO Bucket：
- linchat-media: 媒体文件存储（7天过期）
- linchat-thumbnails: 缩略图存储（永久）

使用方式:
    source /home/dantsinghua/work/linchat/linchat/bin/activate
    cd /home/dantsinghua/work/linchat/backend
    python scripts/init_minio.py

参考: specs/008-multimodal-minicpm/data-model.md
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 加载环境变量
from dotenv import load_dotenv

load_dotenv()

from minio import Minio
from minio.commonconfig import ENABLED
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule, RuleFilter


def init_minio_buckets() -> None:
    """初始化 MinIO Bucket"""
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9010")
    access_key = os.getenv("MINIO_ACCESS_KEY", "")
    secret_key = os.getenv("MINIO_SECRET_KEY", "")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

    if not access_key or not secret_key:
        print("错误: 请设置 MINIO_ACCESS_KEY 和 MINIO_SECRET_KEY 环境变量")
        sys.exit(1)

    print(f"连接到 MinIO: {endpoint}")
    client = Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )

    # 创建媒体文件 Bucket
    bucket_media = os.getenv("MINIO_BUCKET_MEDIA", "linchat-media")
    if not client.bucket_exists(bucket_media):
        client.make_bucket(bucket_media)
        print(f"创建 Bucket: {bucket_media}")

        # 设置生命周期策略（7天过期）
        lifecycle_config = LifecycleConfig(
            [
                Rule(
                    ENABLED,
                    rule_filter=RuleFilter(prefix="media/"),
                    rule_id="expire-media-7-days",
                    expiration=Expiration(days=7),
                ),
            ],
        )
        client.set_bucket_lifecycle(bucket_media, lifecycle_config)
        print(f"设置 {bucket_media} 生命周期策略: 7天过期")
    else:
        print(f"Bucket 已存在: {bucket_media}")

    # 创建缩略图 Bucket
    bucket_thumbnails = os.getenv("MINIO_BUCKET_THUMBNAILS", "linchat-thumbnails")
    if not client.bucket_exists(bucket_thumbnails):
        client.make_bucket(bucket_thumbnails)
        print(f"创建 Bucket: {bucket_thumbnails}")
    else:
        print(f"Bucket 已存在: {bucket_thumbnails}")

    print("MinIO 初始化完成!")


if __name__ == "__main__":
    init_minio_buckets()
