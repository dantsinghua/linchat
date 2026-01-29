"""
用户认证序列化器

参考: behavior-model.md#1.2 用户登录（B_AUTH_002）
"""
from rest_framework import serializers


class CaptchaResponseSerializer(serializers.Serializer):
    """
    验证码响应序列化器

    参考: behavior-model.md#1.1 获取验证码（B_AUTH_001）
    """

    captcha_id = serializers.CharField(
        help_text="验证码ID，用于后续登录请求",
    )
    captcha_image = serializers.CharField(
        help_text="Base64 编码的验证码图片",
    )


class LoginRequestSerializer(serializers.Serializer):
    """
    登录请求序列化器

    参考: behavior-model.md#1.2 用户登录（B_AUTH_002）
    """

    username = serializers.CharField(
        max_length=50,
        help_text="用户名",
    )
    password = serializers.CharField(
        max_length=500,
        help_text="SM4 加密后的密码",
    )
    captcha_id = serializers.CharField(
        max_length=36,
        help_text="验证码ID",
    )
    captcha_code = serializers.CharField(
        max_length=10,
        help_text="用户输入的验证码",
    )

    def validate_username(self, value: str) -> str:
        """验证用户名"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("用户名不能为空")
        return value

    def validate_password(self, value: str) -> str:
        """验证密码"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("密码不能为空")
        return value

    def validate_captcha_code(self, value: str) -> str:
        """验证验证码"""
        value = value.strip()
        if not value:
            raise serializers.ValidationError("验证码不能为空")
        if len(value) != 4:
            raise serializers.ValidationError("验证码格式错误")
        return value


class LoginResponseSerializer(serializers.Serializer):
    """
    登录响应序列化器

    注意: Token 通过 httpOnly Cookie 返回，不在响应体中
    """

    user_id = serializers.IntegerField(
        help_text="用户ID",
    )
    username = serializers.CharField(
        help_text="用户名",
    )
    expire_time = serializers.DateTimeField(
        help_text="Token 无操作过期时间",
    )


class UserInfoSerializer(serializers.Serializer):
    """
    用户信息序列化器

    用于 /auth/me 接口
    """

    user_id = serializers.IntegerField(
        help_text="用户ID",
    )
    username = serializers.CharField(
        help_text="用户名",
    )
    type = serializers.CharField(
        help_text="用户类型（admin/user）",
    )
