import logging
from datetime import timedelta
from io import BytesIO
from typing import BinaryIO, Optional

from django.conf import settings
from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


class MinioService:
    def __init__(self) -> None:
        self._client: Optional[Minio] = None

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
            )
        return self._client

    def upload_file(self, bucket: str, object_name: str, data: BinaryIO, length: int, content_type: str) -> str:
        try:
            self.client.put_object(bucket_name=bucket, object_name=object_name, data=data, length=length, content_type=content_type)
            logger.info(f"上传文件成功: {bucket}/{object_name}")
            return object_name
        except S3Error as e:
            logger.error(f"上传文件失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def upload_bytes(self, bucket: str, object_name: str, data: bytes, content_type: str) -> str:
        return self.upload_file(bucket=bucket, object_name=object_name, data=BytesIO(data), length=len(data), content_type=content_type)

    def download_file(self, bucket: str, object_name: str) -> bytes:
        response = None
        try:
            response = self.client.get_object(bucket, object_name)
            return response.read()
        except S3Error as e:
            logger.error(f"下载文件失败: {bucket}/{object_name}, 错误: {e}")
            raise
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def get_object_stream(self, bucket: str, object_name: str) -> BinaryIO:
        try:
            return self.client.get_object(bucket, object_name)
        except S3Error as e:
            logger.error(f"获取对象流失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def delete_file(self, bucket: str, object_name: str) -> bool:
        try:
            self.client.remove_object(bucket, object_name)
            logger.info(f"删除文件成功: {bucket}/{object_name}")
            return True
        except S3Error as e:
            logger.error(f"删除文件失败: {bucket}/{object_name}, 错误: {e}")
            return False

    def file_exists(self, bucket: str, object_name: str) -> bool:
        try:
            self.client.stat_object(bucket, object_name)
            return True
        except S3Error:
            return False

    def get_presigned_url(self, bucket: str, object_name: str, expires: timedelta = timedelta(hours=1)) -> str:
        try:
            return self.client.presigned_get_object(bucket_name=bucket, object_name=object_name, expires=expires)
        except S3Error as e:
            logger.error(f"生成预签名URL失败: {bucket}/{object_name}, 错误: {e}")
            raise

    def ensure_bucket_exists(self, bucket: str) -> bool:
        try:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                logger.info(f"创建 Bucket: {bucket}")
            return True
        except S3Error as e:
            logger.error(f"确保 Bucket 存在失败: {bucket}, 错误: {e}")
            return False


minio_service = MinioService()
