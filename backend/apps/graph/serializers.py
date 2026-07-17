"""graph 内部端点序列化器。

HusbandReplySerializer：老公 channel 内部回复端点（/api/v1/internal/husband/reply/）入参。
仅文本路由到此端点；image 字段首版忽略（图片消息仍走 wechat 侧原多模态分支）。
"""
from rest_framework import serializers


class HusbandReplySerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)
    channel = serializers.ChoiceField(choices=["wechat"], default="wechat")
    origin_peer = serializers.CharField(max_length=100)
    image = serializers.CharField(required=False, allow_null=True, default=None)  # 首版忽略
