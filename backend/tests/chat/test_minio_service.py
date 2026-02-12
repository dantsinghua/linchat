"""
MinioService 单元测试

参考: specs/008-multimodal-minicpm/tasks.md#T011

覆盖:
- upload_file: 文件上传
- upload_bytes: 字节数据上传
- download_file: 文件下载
- delete_file: 文件删除
- file_exists: 文件存在检查
- get_presigned_url: 预签名 URL 生成
- ensure_bucket_exists: Bucket 创建

覆盖率要求: 服务层 ≥ 95%
"""

from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from minio.error import S3Error

from apps.chat.services.minio_service import MinioService


class TestMinioService:
    """MinioService 测试类"""

    @pytest.fixture
    def minio_service(self):
        """创建 MinioService 实例"""
        return MinioService()

    @pytest.fixture
    def mock_client(self, minio_service):
        """Mock MinIO 客户端"""
        mock = MagicMock()
        minio_service._client = mock
        return mock

    # ============ upload_file 测试 ============

    def test_upload_file_success(self, minio_service, mock_client):
        """测试文件上传成功"""
        data = BytesIO(b"test content")
        result = minio_service.upload_file(
            bucket="test-bucket",
            object_name="test/file.txt",
            data=data,
            length=12,
            content_type="text/plain",
        )

        assert result == "test/file.txt"
        mock_client.put_object.assert_called_once_with(
            bucket_name="test-bucket",
            object_name="test/file.txt",
            data=data,
            length=12,
            content_type="text/plain",
        )

    def test_upload_file_s3_error(self, minio_service, mock_client):
        """测试文件上传 S3 错误"""
        mock_client.put_object.side_effect = S3Error(
            code="NoSuchBucket",
            message="The specified bucket does not exist",
            resource="test-bucket",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        with pytest.raises(S3Error):
            minio_service.upload_file(
                bucket="test-bucket",
                object_name="test/file.txt",
                data=BytesIO(b"test"),
                length=4,
                content_type="text/plain",
            )

    # ============ upload_bytes 测试 ============

    def test_upload_bytes_success(self, minio_service, mock_client):
        """测试字节数据上传成功"""
        data = b"test content bytes"
        result = minio_service.upload_bytes(
            bucket="test-bucket",
            object_name="test/bytes.txt",
            data=data,
            content_type="text/plain",
        )

        assert result == "test/bytes.txt"
        mock_client.put_object.assert_called_once()
        call_args = mock_client.put_object.call_args
        assert call_args.kwargs["bucket_name"] == "test-bucket"
        assert call_args.kwargs["object_name"] == "test/bytes.txt"
        assert call_args.kwargs["length"] == len(data)

    # ============ download_file 测试 ============

    def test_download_file_success(self, minio_service, mock_client):
        """测试文件下载成功"""
        mock_response = MagicMock()
        mock_response.read.return_value = b"downloaded content"
        mock_client.get_object.return_value = mock_response

        result = minio_service.download_file(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result == b"downloaded content"
        mock_client.get_object.assert_called_once_with("test-bucket", "test/file.txt")
        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()

    def test_download_file_not_found(self, minio_service, mock_client):
        """测试文件下载不存在"""
        mock_client.get_object.side_effect = S3Error(
            code="NoSuchKey",
            message="The specified key does not exist",
            resource="test/file.txt",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        with pytest.raises(S3Error):
            minio_service.download_file(
                bucket="test-bucket",
                object_name="test/file.txt",
            )

    # ============ get_object_stream 测试 ============

    def test_get_object_stream_success(self, minio_service, mock_client):
        """测试获取对象流成功"""
        mock_stream = MagicMock()
        mock_client.get_object.return_value = mock_stream

        result = minio_service.get_object_stream(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result == mock_stream
        mock_client.get_object.assert_called_once_with("test-bucket", "test/file.txt")

    def test_get_object_stream_error(self, minio_service, mock_client):
        """测试获取对象流错误"""
        mock_client.get_object.side_effect = S3Error(
            code="NoSuchKey",
            message="The specified key does not exist",
            resource="test/file.txt",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        with pytest.raises(S3Error):
            minio_service.get_object_stream(
                bucket="test-bucket",
                object_name="test/file.txt",
            )

    # ============ delete_file 测试 ============

    def test_delete_file_success(self, minio_service, mock_client):
        """测试文件删除成功"""
        result = minio_service.delete_file(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result is True
        mock_client.remove_object.assert_called_once_with("test-bucket", "test/file.txt")

    def test_delete_file_error(self, minio_service, mock_client):
        """测试文件删除失败"""
        mock_client.remove_object.side_effect = S3Error(
            code="AccessDenied",
            message="Access Denied",
            resource="test/file.txt",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        result = minio_service.delete_file(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result is False

    # ============ file_exists 测试 ============

    def test_file_exists_true(self, minio_service, mock_client):
        """测试文件存在"""
        mock_client.stat_object.return_value = MagicMock()

        result = minio_service.file_exists(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result is True
        mock_client.stat_object.assert_called_once_with("test-bucket", "test/file.txt")

    def test_file_exists_false(self, minio_service, mock_client):
        """测试文件不存在"""
        mock_client.stat_object.side_effect = S3Error(
            code="NoSuchKey",
            message="The specified key does not exist",
            resource="test/file.txt",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        result = minio_service.file_exists(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result is False

    # ============ get_presigned_url 测试 ============

    def test_get_presigned_url_success(self, minio_service, mock_client):
        """测试获取预签名 URL 成功"""
        expected_url = "https://minio.example.com/test-bucket/test/file.txt?signed"
        mock_client.presigned_get_object.return_value = expected_url

        result = minio_service.get_presigned_url(
            bucket="test-bucket",
            object_name="test/file.txt",
            expires=timedelta(hours=2),
        )

        assert result == expected_url
        mock_client.presigned_get_object.assert_called_once_with(
            bucket_name="test-bucket",
            object_name="test/file.txt",
            expires=timedelta(hours=2),
        )

    def test_get_presigned_url_default_expires(self, minio_service, mock_client):
        """测试获取预签名 URL 默认过期时间"""
        expected_url = "https://minio.example.com/signed"
        mock_client.presigned_get_object.return_value = expected_url

        result = minio_service.get_presigned_url(
            bucket="test-bucket",
            object_name="test/file.txt",
        )

        assert result == expected_url
        call_args = mock_client.presigned_get_object.call_args
        assert call_args.kwargs["expires"] == timedelta(hours=1)

    def test_get_presigned_url_error(self, minio_service, mock_client):
        """测试获取预签名 URL 错误"""
        mock_client.presigned_get_object.side_effect = S3Error(
            code="NoSuchKey",
            message="The specified key does not exist",
            resource="test/file.txt",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        with pytest.raises(S3Error):
            minio_service.get_presigned_url(
                bucket="test-bucket",
                object_name="test/file.txt",
            )

    # ============ ensure_bucket_exists 测试 ============

    def test_ensure_bucket_exists_already_exists(self, minio_service, mock_client):
        """测试 Bucket 已存在"""
        mock_client.bucket_exists.return_value = True

        result = minio_service.ensure_bucket_exists("test-bucket")

        assert result is True
        mock_client.bucket_exists.assert_called_once_with("test-bucket")
        mock_client.make_bucket.assert_not_called()

    def test_ensure_bucket_exists_create_new(self, minio_service, mock_client):
        """测试创建新 Bucket"""
        mock_client.bucket_exists.return_value = False

        result = minio_service.ensure_bucket_exists("new-bucket")

        assert result is True
        mock_client.bucket_exists.assert_called_once_with("new-bucket")
        mock_client.make_bucket.assert_called_once_with("new-bucket")

    def test_ensure_bucket_exists_error(self, minio_service, mock_client):
        """测试 Bucket 创建失败"""
        mock_client.bucket_exists.side_effect = S3Error(
            code="AccessDenied",
            message="Access Denied",
            resource="test-bucket",
            request_id="test-request-id",
            host_id="test-host-id",
            response=None,
        )

        result = minio_service.ensure_bucket_exists("test-bucket")

        assert result is False

    # ============ client 懒加载测试 ============

    @patch("apps.chat.services.minio_service.settings")
    @patch("apps.chat.services.minio_service.Minio")
    def test_client_lazy_loading(self, mock_minio_class, mock_settings):
        """测试客户端懒加载"""
        mock_settings.MINIO_ENDPOINT = "localhost:9000"
        mock_settings.MINIO_ACCESS_KEY = "test-key"
        mock_settings.MINIO_SECRET_KEY = "test-secret"
        mock_settings.MINIO_SECURE = False

        service = MinioService()
        assert service._client is None

        # 首次访问触发初始化
        _ = service.client
        assert service._client is not None
        mock_minio_class.assert_called_once_with(
            endpoint="localhost:9000",
            access_key="test-key",
            secret_key="test-secret",
            secure=False,
        )

        # 再次访问不会重复初始化
        _ = service.client
        assert mock_minio_class.call_count == 1
