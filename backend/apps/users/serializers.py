"""用户认证序列化器 — 输入验证"""

import re

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


class CreateMemberSerializer(serializers.Serializer):
    """创建家庭成员请求验证"""

    username = serializers.CharField(min_length=3, max_length=50)
    password = serializers.CharField(max_length=500)  # SM4 加密密码
    member_type = serializers.ChoiceField(choices=["member", "guest"])
    audio = serializers.FileField()  # 声纹录音文件

    def validate_username(self, value: str) -> str:
        value = value.strip()
        if not re.match(r"^[a-zA-Z0-9_]+$", value):
            raise serializers.ValidationError("用户名仅支持字母、数字和下划线")
        return value


class MemberListSerializer(serializers.Serializer):
    """成员列表输出序列化器"""

    user_id = serializers.IntegerField()
    username = serializers.CharField()
    member_type = serializers.CharField()
    status = serializers.IntegerField()
    guest_expires_at = serializers.DateTimeField(allow_null=True)
    is_expired = serializers.BooleanField()
    created_time = serializers.DateTimeField()
