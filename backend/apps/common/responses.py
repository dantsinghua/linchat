"""统一响应格式

响应格式: {"code": "SUCCESS", "data": {}, "message": "操作成功"}
"""

from typing import Any

from django.http import JsonResponse
from rest_framework import status
from rest_framework.response import Response


# ============ Django JsonResponse 版本 ============


def api_response(
    data: Any = None, message: str = "操作成功",
    code: str = "SUCCESS", status_code: int = 200,
) -> JsonResponse:
    return JsonResponse({"code": code, "message": message, "data": data}, status=status_code)


def error_response(
    message: str = "操作失败", code: str = "ERROR",
    status_code: int = 400, extra: dict | None = None,
) -> JsonResponse:
    body: dict = {"code": code, "message": message, "data": None}
    if extra:
        body.update(extra)
    return JsonResponse(body, status=status_code)


# ============ DRF Response 版本 ============


def _resp(code: str, message: str, data: Any, http_status: int) -> Response:
    return Response({"code": code, "message": message, "data": data}, status=http_status)


class ApiResponse:
    """统一 API 响应封装"""

    CODE_SUCCESS = "SUCCESS"
    CODE_ERROR = "ERROR"
    CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
    CODE_NOT_FOUND = "NOT_FOUND"
    CODE_UNAUTHORIZED = "UNAUTHORIZED"
    CODE_FORBIDDEN = "FORBIDDEN"

    @staticmethod
    def success(data: Any = None, message: str = "操作成功", status_code: int = status.HTTP_200_OK) -> Response:
        return _resp(ApiResponse.CODE_SUCCESS, message, data, status_code)

    @staticmethod
    def created(data: Any = None, message: str = "创建成功") -> Response:
        return _resp(ApiResponse.CODE_SUCCESS, message, data, status.HTTP_201_CREATED)

    @staticmethod
    def error(message: str = "操作失败", code: str = CODE_ERROR, data: Any = None, status_code: int = status.HTTP_400_BAD_REQUEST) -> Response:
        return _resp(code, message, data, status_code)

    @staticmethod
    def validation_error(message: str = "参数验证失败", errors: dict | list | None = None) -> Response:
        return _resp(ApiResponse.CODE_VALIDATION_ERROR, message, {"errors": errors} if errors else None, status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def not_found(message: str = "资源不存在") -> Response:
        return _resp(ApiResponse.CODE_NOT_FOUND, message, None, status.HTTP_404_NOT_FOUND)

    @staticmethod
    def unauthorized(message: str = "请先登录") -> Response:
        return _resp(ApiResponse.CODE_UNAUTHORIZED, message, None, status.HTTP_401_UNAUTHORIZED)

    @staticmethod
    def forbidden(message: str = "无权限访问") -> Response:
        return _resp(ApiResponse.CODE_FORBIDDEN, message, None, status.HTTP_403_FORBIDDEN)

    @staticmethod
    def paginated(items: list, total: int, page: int, page_size: int, message: str = "查询成功") -> Response:
        return _resp(ApiResponse.CODE_SUCCESS, message, {
            "items": items, "total": total, "page": page,
            "pageSize": page_size, "totalPages": (total + page_size - 1) // page_size,
        }, status.HTTP_200_OK)

    @staticmethod
    def cursor_paginated(items: list, next_cursor: str | int | None = None, has_more: bool = False, message: str = "查询成功") -> Response:
        return _resp(ApiResponse.CODE_SUCCESS, message, {
            "items": items, "nextCursor": next_cursor, "hasMore": has_more,
        }, status.HTTP_200_OK)
