"""
模型配置序列化器

参考:
- specs/003-model-config/spec.md FR-005, FR-006, FR-009, FR-012
- specs/003-model-config/contracts/api.yaml
"""
from rest_framework import serializers


class ModelResponseSerializer(serializers.Serializer):
    """模型配置响应序列化器

    用于 GET 接口，API Key 已脱敏。
    参考: contracts/api.yaml#ModelResponse
    """

    id = serializers.IntegerField()
    type = serializers.CharField()
    name = serializers.CharField()
    url = serializers.CharField()
    api_key = serializers.CharField(help_text="脱敏展示")
    max_context_window = serializers.IntegerField()
    max_input_tokens = serializers.IntegerField()
    max_output_tokens = serializers.IntegerField()
    temperature = serializers.FloatField(allow_null=True)
    top_p = serializers.FloatField(allow_null=True)
    frequency_penalty = serializers.FloatField(allow_null=True)
    presence_penalty = serializers.FloatField(allow_null=True)
    embedding_dimensions = serializers.IntegerField(allow_null=True)
    is_active = serializers.BooleanField()
    effective_context_window = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ModelUpdateSerializer(serializers.Serializer):
    """模型配置更新序列化器

    用于 PUT 接口。
    - type / is_active 为只读，不包含在更新字段中
    - 选填字段 required=False, allow_null=True
    参考: contracts/api.yaml#ModelUpdateRequest, spec.md FR-005, FR-012
    """

    name = serializers.CharField(max_length=100)
    url = serializers.CharField(max_length=500)
    api_key = serializers.CharField(
        help_text="新密钥或脱敏值（包含 **** 时保留原值）"
    )
    max_context_window = serializers.IntegerField(min_value=1)
    max_input_tokens = serializers.IntegerField(min_value=1)
    max_output_tokens = serializers.IntegerField(min_value=1)

    # 选填采样参数
    temperature = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=2
    )
    top_p = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=1
    )
    frequency_penalty = serializers.FloatField(
        required=False, allow_null=True, min_value=-2, max_value=2
    )
    presence_penalty = serializers.FloatField(
        required=False, allow_null=True, min_value=-2, max_value=2
    )
    embedding_dimensions = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )

    def validate_api_key(self, value: str) -> str:
        """校验 API Key

        脱敏值（包含 ****）跳过长度校验。
        新密钥最少 12 字符。
        参考: spec.md FR-005, FR-012
        """
        if "****" in value:
            # 脱敏值，跳过校验，Service 层处理保留逻辑
            return value
        if len(value) < 12:
            raise serializers.ValidationError("API Key 最少 12 个字符")
        return value

    def validate_max_context_window(self, value: int) -> int:
        """确保为正整数（不接受小数，serializer IntegerField 自动处理）"""
        if value != int(value):
            raise serializers.ValidationError("必须为整数")
        return value

    def validate(self, data: dict) -> dict:
        """跨字段校验

        非 embedding 类型的模型，embedding_dimensions 必须为 NULL。
        参考: spec.md 假设中 embedding_dimensions 仅对 embedding 类型有意义
        """
        # 注意：type 不在更新字段中，需要从上下文获取
        model_type = self.context.get("model_type")
        if model_type != "embedding" and data.get("embedding_dimensions") is not None:
            raise serializers.ValidationError(
                {"embedding_dimensions": "非向量模型的 embedding_dimensions 必须为空"}
            )
        return data
