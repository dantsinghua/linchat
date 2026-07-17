"""graph 内部端点（设备 token 鉴权，跳过 cookie 中间件）。不属对外 API 契约。

/api/v1/internal/husband/reply/ → POST 老公 channel 聚合回复（wechat 外部来源）

安全红线：/api/v1/internal/ 在 PUBLIC_PATHS 中已跳过 cookie 中间件，
因此本 view 必须自行校验设备 token，token 缺失/无效一律返回 401 且不调 execute。
user_id 一律由 token 派生，绝不从请求体读取（隔离粒度永远 user_id）。
"""
import logging
import uuid

from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.common.responses import ApiResponse
from apps.graph.serializers import HusbandReplySerializer
from apps.graph.services import AgentService
from apps.voice.services.device_service import device_service

logger = logging.getLogger(__name__)


async def _aggregate_reply(user_id: int, message: str, channel: str) -> str:
    """驱动 AgentService.execute 并把 content 帧累加为完整回复文本。

    - content 帧：唯一正文 token，累加。
    - error 帧：抛 → 由 view 转 502。
    - done/interrupted/context_compacting/context_compacted 等：忽略。
    - AgentService 内部抛 LLMException / 其它异常：向上传播 → view 转 502。
    thread_id=user_{id}_wechat 仅作 execution/Langfuse 标签级分流（层3），不隔离历史。
    """
    request_id = uuid.uuid4().hex
    thread_id = f"user_{user_id}_wechat"
    parts: list[str] = []
    async for chunk in AgentService.execute(
        user_id=user_id, thread_id=thread_id, request_id=request_id,
        user_message=message, attachment_uuids=None, channel=channel,
    ):
        if chunk.type == "content":
            parts.append(chunk.content)
        elif chunk.type == "error":
            raise RuntimeError(f"agent error chunk: {chunk.content}")
    reply = "".join(parts).strip()
    if not reply:
        raise RuntimeError("empty reply")
    return reply


@api_view(["POST"])
def husband_reply(request: Request) -> Response:
    token = request.META.get("HTTP_X_DEVICE_TOKEN", "")
    auth = async_to_sync(device_service.authenticate_by_token)(token)
    if not auth:
        return ApiResponse.unauthorized(message="设备 token 无效")
    user_id = auth["user_id"]                        # token 派生，不从请求体读取
    s = HusbandReplySerializer(data=request.data)
    if not s.is_valid():
        return ApiResponse.validation_error(errors=s.errors)
    d = s.validated_data
    channel, origin_peer = d["channel"], d["origin_peer"]
    try:
        reply = async_to_sync(_aggregate_reply)(user_id, d["message"], channel)
    except Exception as e:
        logger.warning("husband_reply failed: user_id=%s peer=%s err=%s", user_id, origin_peer, e)
        return ApiResponse.error(message="agent 生成失败", status_code=502)
    logger.info("husband_reply ok: user_id=%s channel=%s peer=%s len=%d", user_id, channel, origin_peer, len(reply))
    # 层1 回声令牌：回带 channel + origin_peer，供 wechat 侧校验防串台。
    return ApiResponse.success(data={"reply": reply, "channel": channel, "origin_peer": origin_peer})
