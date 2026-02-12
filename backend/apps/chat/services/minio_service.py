"""
MinIO 对象存储服务

参考: specs/008-multimodal-minicpm/research.md#1 媒体存储方案
"""

import logging
from datetime import timedelta
from io import BytesIO
from typing import BinaryIO, Optional

from django.conf import settings
from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


class MinioService:
    """MinIO 对象存储服务

    负责媒体文件的上传、下载、删除和预签名 URL 生成。
    """

    def __init__(self) -> None:
        """初始化 MinIO 客户端"""
        self._client: Optional[Minio] = None

    @property
    def client(self) -> Minio:
        """懒加载 MinIO 客户端"""
        if self._client is None:
            self._client = Minio(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
            )
        return self._client

    def upload_file(
        self,
        bucket: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> str:
        """上传文件到 MinIO

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）
            data: 文件数据流
            length: 文件大小
            content_type: 内容类型

        Returns:
            上传后的对象名称
        """
        try:
            self.client.put_object(
                bucket_name=bucket,
                object_name=object_name,
                data=data,
                length=length,
                content_type=content_type,
            )
            logger.info(f"上传文件成功: {bucket}/{object_name}")
            return object_name
        except S3Error as e:
            logger.error(f"上传文件失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def upload_bytes(
        self,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
    ) -> str:
        """上传字节数据到 MinIO

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）
            data: 字节数据
            content_type: 内容类型

        Returns:
            上传后的对象名称
        """
        return self.upload_file(
            bucket=bucket,
            object_name=object_name,
            data=BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def download_file(self, bucket: str, object_name: str) -> bytes:
        """从 MinIO 下载文件

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）

        Returns:
            文件字节数据
        """
        try:
            response = self.client.get_object(bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as e:
            logger.error(f"下载文件失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def get_object_stream(self, bucket: str, object_name: str) -> BinaryIO:
        """获取对象流（用于大文件流式读取）

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）

        Returns:
            文件流对象（需要调用方关闭）
        """
        try:
            return self.client.get_object(bucket, object_name)
        except S3Error as e:
            logger.error(f"获取对象流失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def delete_file(self, bucket: str, object_name: str) -> bool:
        """从 MinIO 删除文件

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）

        Returns:
            是否删除成功
        """
        try:
            self.client.remove_object(bucket, object_name)
            logger.info(f"删除文件成功: {bucket}/{object_name}")
            return True
        except S3Error as e:
            logger.error(f"删除文件失败: {bucket}/{object_name}, 错误: {e}")
            return False

    def file_exists(self, bucket: str, object_name: str) -> bool:
        """检查文件是否存在

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）

        Returns:
            文件是否存在
        """
        try:
            self.client.stat_object(bucket, object_name)
            return True
        except S3Error:
            return False

    def get_presigned_url(
        self,
        bucket: str,
        object_name: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """获取预签名下载 URL

        Args:
            bucket: Bucket 名称
            object_name: 对象名称（路径）
            expires: 过期时间

        Returns:
            预签名 URL
        """
        try:
            return self.client.presigned_get_object(
                bucket_name=bucket,
                object_name=object_name,
                expires=expires,
            )
        except S3Error as e:
            logger.error(f"生成预签名URL失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def ensure_bucket_exists(self, bucket: str) -> bool:
        """确保 Bucket 存在，不存在则创建

        Args:
            bucket: Bucket 名称

        Returns:
            是否成功
        """
        try:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                logger.info(f"创建 Bucket: {bucket}")
            return True
        except S3Error as e:
            logger.error(f"确保 Bucket 存在失败: {bucket}, 错误: {e}")
            return False


# 单例实例
minio_service = MinioService()
