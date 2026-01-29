"""
模型配置自定义权限类

参考: spec.md FR-016, FR-017
基于 User.type == 'admin' 判定，不使用 Django 内置 is_staff/is_superuser
"""
from rest_framework.permissions import BasePermission


class IsAdminUser(BasePermission):
    """仅允许管理员用户访问

    通过 request.user_type 判定（由 TokenAuthMiddleware 设置）。
    """

    message = "权限不足，仅管理员可访问"

    def has_permission(self, request, view) -> bool:
        user_type = getattr(request, "user_type", None)
        return user_type == "admin"
