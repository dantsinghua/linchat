"""用户认证序列化器 — 仅保留有验证逻辑的输入序列化器"""
from rest_framework import serializers


class LoginRequestSerializer(serializers.Serializer):
    """登录请求验证"""

    username = serializers.CharField(max_length=50)
    password = serializers.CharField(max_length=500)
    captcha_id = serializers.CharField(max_length=36)
    captcha_code = serializers.CharField(max_length=10)

    def validate_username(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("用户名不能为空")
        return value

    def validate_password(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("密码不能为空")
        return value

    def validate_captcha_code(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("验证码不能为空")
        if len(value) != 4:
            raise serializers.ValidationError("验证码格式错误")
        return value
