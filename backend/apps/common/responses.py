"""
统一响应格式

参考: constitution.md#1.2 接口设计
响应格式: {"code": "SUCCESS", "data": {}, "message": "操作成功"}
"""
from typing import Any, TypeVar

from rest_framework import status
from rest_framework.response import Response

T = TypeVar("T")


class ApiResponse:
    """统一 API 响应封装"""

    # 成功响应码
    CODE_SUCCESS = "SUCCESS"

    # 错误响应码
    CODE_ERROR = "ERROR"
    CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
    CODE_NOT_FOUND = "NOT_FOUND"
    CODE_UNAUTHORIZED = "UNAUTHORIZED"
    CODE_FORBIDDEN = "FORBIDDEN"

    @staticmethod
    def success(
        data: Any = None,
        message: str = "操作成功",
        status_code: int = status.HTTP_200_OK,
    ) -> Response:
        """成功响应"""
        return Response(
            {
                "code": ApiResponse.CODE_SUCCESS,
                "message": message,
                "data": data,
            },
            status=status_code,
        )

    @staticmethod
    def created(
        data: Any = None,
        message: str = "创建成功",
    ) -> Response:
        """创建成功响应"""
        return Response(
            {
                "code": ApiResponse.CODE_SUCCESS,
                "message": message,
                "data": data,
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def error(
        message: str = "操作失败",
        code: str = CODE_ERROR,
        data: Any = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> Response:
        """错误响应"""
        return Response(
            {
                "code": code,
                "message": message,
                "data": data,
            },
            status=status_code,
        )

    @staticmethod
    def validation_error(
        message: str = "参数验证失败",
        errors: dict | list | None = None,
    ) -> Response:
        """参数验证错误响应"""
        return Response(
            {
                "code": ApiResponse.CODE_VALIDATION_ERROR,
                "message": message,
                "data": {"errors": errors} if errors else None,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    @staticmethod
    def not_found(
        message: str = "资源不存在",
    ) -> Response:
        """资源不存在响应"""
        return Response(
            {
                "code": ApiResponse.CODE_NOT_FOUND,
                "message": message,
                "data": None,
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def unauthorized(
        message: str = "请先登录",
    ) -> Response:
        """未授权响应"""
        return Response(
            {
                "code": ApiResponse.CODE_UNAUTHORIZED,
                "message": message,
                "data": None,
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )

    @staticmethod
    def forbidden(
        message: str = "无权限访问",
    ) -> Response:
        """禁止访问响应"""
        return Response(
            {
                "code": ApiResponse.CODE_FORBIDDEN,
                "message": message,
                "data": None,
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    @staticmethod
    def paginated(
        items: list,
        total: int,
        page: int,
        page_size: int,
        message: str = "查询成功",
    ) -> Response:
        """分页响应"""
        return Response(
            {
                "code": ApiResponse.CODE_SUCCESS,
                "message": message,
                "data": {
                    "items": items,
                    "total": total,
                    "page": page,
                    "pageSize": page_size,
                    "totalPages": (total + page_size - 1) // page_size,
                },
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def cursor_paginated(
        items: list,
        next_cursor: str | int | None = None,
        has_more: bool = False,
        message: str = "查询成功",
    ) -> Response:
        """游标分页响应

        参考: behavior-model.md#2.3 加载历史消息
        """
        return Response(
            {
                "code": ApiResponse.CODE_SUCCESS,
                "message": message,
                "data": {
                    "items": items,
                    "nextCursor": next_cursor,
                    "hasMore": has_more,
                },
            },
            status=status.HTTP_200_OK,
        )
