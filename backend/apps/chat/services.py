"""
聊天服务

参考:
- behavior-model.md#2.1 发送消息并获取响应（B_CHAT_001）
- behavior-model.md#2.2 执行LangGraph Agent（B_CHAT_002）
- behavior-model.md#2.3 加载历史消息（B_CHAT_003）
- behavior-model.md#2.5 继续生成（B_CHAT_005）
- rule-model.md#R_LLM_RETRY_001 LLM重试策略规则
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Optional

from django.conf import settings
from django.utils import timezone
from langchain_core.messages import AIMessage, HumanMessage
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

from apps.chat.agent import (create_chat_agent, get_agent_config,
                             get_checkpointer, get_thread_id)
from apps.chat.models import LangGraphExecution, Message
from apps.chat.repositories import execution_repo, message_repo
from apps.common.exceptions import (EmptyMessageException, LLMConnectionError,
                                    LLMContentFilterError, LLMException,
                                    LLMInvalidResponseError,
                                    LLMQuotaExceededError, LLMRateLimitError,
                                    LLMTimeoutError, MessageTooLongException)
from apps.models.services import model_service
from apps.users.repositories import user_repo

logger = logging.getLogger(__name__)


def _get_language_model_name() -> str:
    """从数据库获取激活的语言模型名称"""
    config = model_service.get_active_model("language")
    return config["name"] if config else "unknown"


# ============ 数据类 ============


@dataclass
class StreamChunk:
    """流式响应块"""

    type: str  # content, done, error, interrupted
    content: str
    message_id: Optional[int] = None
    request_id: Optional[str] = None  # 首个 chunk 返回，用于前端停止/继续生成


@dataclass
class MessageVO:
    """消息视图对象"""

    message_id: int
    message_uuid: str
    role: str
    content: str
    status: int
    sequence: int
    created_time: str
    request_id: Optional[str] = None
    model_name: Optional[str] = None
    response_time_ms: Optional[int] = None

    @classmethod
    def from_entity(cls, message: Message) -> "MessageVO":
        """从实体转换"""
        return cls(
            message_id=message.message_id,
            message_uuid=message.message_uuid,
            role=message.role,
            content=message.content,
            status=message.status,
            sequence=message.sequence,
            created_time=message.created_time.isoformat(),
            request_id=message.request_id,
            model_name=message.model_name,
            response_time_ms=message.response_time_ms,
        )


# ============ 活跃生成会话管理 ============

# 存储正在生成中的会话，用于停止生成
# key: request_id, value: asyncio.Event (设置时表示应该停止)
_active_generations: dict[str, asyncio.Event] = {}


def register_generation(request_id: str) -> asyncio.Event:
    """注册一个新的生成会话"""
    stop_event = asyncio.Event()
    _active_generations[request_id] = stop_event
    return stop_event


def unregister_generation(request_id: str) -> None:
    """取消注册生成会话"""
    _active_generations.pop(request_id, None)


def get_stop_event(request_id: str) -> Optional[asyncio.Event]:
    """获取停止事件"""
    return _active_generations.get(request_id)


def signal_stop(request_id: str) -> bool:
    """发送停止信号"""
    stop_event = _active_generations.get(request_id)
    if stop_event:
        stop_event.set()
        return True
    return False


# ============ LLM 异常映射 ============


def map_llm_exception(e: Exception) -> LLMException:
    """
    将原始异常映射为 LLM 异常

    参考: rule-model.md#R_LLM_RETRY_001
    """
    error_str = str(e).lower()

    # 连接错误
    if any(
        kw in error_str for kw in ["connection", "connect", "network", "unreachable"]
    ):
        return LLMConnectionError()

    # 超时错误
    if any(kw in error_str for kw in ["timeout", "timed out"]):
        return LLMTimeoutError()

    # 频率限制
    if any(kw in error_str for kw in ["rate limit", "too many requests", "429"]):
        return LLMRateLimitError()

    # 内容过滤
    if any(
        kw in error_str for kw in ["content filter", "content policy", "moderation"]
    ):
        return LLMContentFilterError()

    # 配额用尽
    if any(kw in error_str for kw in ["quota", "insufficient", "billing"]):
        return LLMQuotaExceededError()

    # 默认为无效响应（可重试）
    return LLMInvalidResponseError(str(e))


# ============ 聊天服务 ============


class ChatService:
    """
    聊天服务

    参考: behavior-model.md#2.1 发送消息并获取响应（B_CHAT_001）
    """

    @staticmethod
    async def send_message(
        user_id: int, content: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        发送消息并获取流式响应

        参考: behavior-model.md#2.1

        Args:
            user_id: 用户ID
            content: 消息内容

        Yields:
            StreamChunk: 流式响应块

        Raises:
            EmptyMessageException: 空消息
            MessageTooLongException: 消息过长
        """
        # [R_MSG_002] 空消息拦截
        content = content.strip()
        if not content:
            raise EmptyMessageException("消息内容不能为空")

        # [R_MSG_001] 消息长度限制
        if len(content) > settings.MAX_MESSAGE_LENGTH:
            raise MessageTooLongException(
                f"消息长度不能超过{settings.MAX_MESSAGE_LENGTH}字符"
            )

        # [R_DATA_001] thread_id 包含 user_id 确保数据隔离
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        thread_id = get_thread_id(user_id)

        # 调用 Agent 执行
        async for chunk in AgentService.execute(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            user_message=content,
        ):
            yield chunk

    @staticmethod
    async def stop_generation(user_id: int, request_id: str) -> bool:
        """
        停止生成

        参考: spec.md US2场景9 - 停止按钮逻辑

        Args:
            user_id: 用户ID
            request_id: 请求ID

        Returns:
            bool: 是否成功发送停止信号
        """
        # 发送停止信号
        success = signal_stop(request_id)
        if success:
            logger.info(f"Stop signal sent for request {request_id}")
        return success

    @staticmethod
    async def resume_generation(
        user_id: int, request_id: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        继续生成（从中断处恢复）

        参考: behavior-model.md#2.5 继续生成（B_CHAT_005）

        Args:
            user_id: 用户ID
            request_id: 原请求ID

        Yields:
            StreamChunk: 流式响应块
        """
        # 1. 查询被中断的消息
        message = await message_repo.get_by_request_id(request_id, user_id)
        if not message:
            yield StreamChunk(type="error", content="消息不存在")
            return

        if message.status != Message.STATUS_INTERRUPTED:
            yield StreamChunk(type="error", content="该消息不可继续生成")
            return

        thread_id = get_thread_id(user_id)

        # 更新消息状态为生成中
        await message_repo.update_status(message.message_id, user_id, Message.STATUS_GENERATING)

        # 继续生成
        async for chunk in AgentService.resume(
            user_id=user_id, thread_id=thread_id, request_id=request_id, message=message
        ):
            yield chunk

    @staticmethod
    async def reconnect_stream(
        user_id: int, request_id: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        重连流式响应（用于页面刷新时重连生成中的消息）

        参考: behavior-model.md#2.4 流式响应重连（B_CHAT_004）

        Args:
            user_id: 用户ID
            request_id: 请求ID

        Yields:
            StreamChunk: 流式响应块
        """
        # 1. 查询生成中的消息
        message = await message_repo.get_by_request_id(request_id, user_id)
        if not message:
            yield StreamChunk(type="error", content="消息不存在")
            return

        if message.status != Message.STATUS_GENERATING:
            # 消息已不在生成中，返回当前状态
            if message.status == Message.STATUS_NORMAL:
                yield StreamChunk(
                    type="done",
                    content="",
                    message_id=message.message_id,
                )
            elif message.status == Message.STATUS_INTERRUPTED:
                yield StreamChunk(
                    type="interrupted",
                    content="[已中断]",
                    message_id=message.message_id,
                )
            elif message.status == Message.STATUS_FAILED:
                yield StreamChunk(type="error", content="生成失败")
            return

        # 2. 检查是否有活跃的生成任务
        stop_event = get_stop_event(request_id)
        if not stop_event:
            # 没有活跃的生成任务，可能服务重启了
            # 将消息标记为中断状态
            await message_repo.update_status(
                message.message_id, user_id, Message.STATUS_INTERRUPTED
            )
            yield StreamChunk(
                type="interrupted",
                content="[已中断]",
                message_id=message.message_id,
            )
            return

        # 3. 发送当前已有的内容
        if message.content:
            yield StreamChunk(
                type="content",
                content=message.content,
                message_id=message.message_id,
            )

        # 4. 等待生成完成或中断
        # 由于原生成流程会继续推送到原 SSE 连接，
        # 这里我们通过轮询数据库状态来获取最终结果
        import asyncio
        max_wait = 300  # 最大等待5分钟
        poll_interval = 0.5  # 轮询间隔0.5秒
        last_content = message.content or ""

        for _ in range(int(max_wait / poll_interval)):
            await asyncio.sleep(poll_interval)

            # 重新查询消息状态
            updated_msg = await message_repo.get_by_request_id(request_id, user_id)
            if not updated_msg:
                yield StreamChunk(type="error", content="消息不存在")
                return

            # 推送新增的内容
            if updated_msg.content and len(updated_msg.content) > len(last_content):
                new_content = updated_msg.content[len(last_content):]
                # 移除末尾的 [已中断] 标记（如果有）
                if new_content.endswith("[已中断]"):
                    new_content = new_content[:-6]
                if new_content:
                    yield StreamChunk(
                        type="content",
                        content=new_content,
                        message_id=updated_msg.message_id,
                    )
                last_content = updated_msg.content.replace("[已中断]", "")

            # 检查是否已完成
            if updated_msg.status == Message.STATUS_NORMAL:
                yield StreamChunk(
                    type="done",
                    content="",
                    message_id=updated_msg.message_id,
                )
                return
            elif updated_msg.status == Message.STATUS_INTERRUPTED:
                yield StreamChunk(
                    type="interrupted",
                    content="[已中断]",
                    message_id=updated_msg.message_id,
                )
                return
            elif updated_msg.status == Message.STATUS_FAILED:
                yield StreamChunk(type="error", content="生成失败")
                return

        # 超时
        yield StreamChunk(type="error", content="重连超时")


class HistoryService:
    """
    历史消息服务

    参考: behavior-model.md#2.3 加载历史消息（B_CHAT_003）
    """

    @staticmethod
    async def load_messages(
        user_id: int, limit: int = 50, before_sequence: Optional[int] = None
    ) -> list[MessageVO]:
        """
        加载历史消息

        参考: behavior-model.md#2.3

        Args:
            user_id: 用户ID
            limit: 返回数量限制（最大100）
            before_sequence: 游标序号（用于分页）

        Returns:
            list[MessageVO]: 消息列表（按时间正序）

        Note:
            [R_DATA_001] 通过 user_id 过滤确保数据隔离
        """
        # 限制最大返回数量
        limit = min(limit, 100)

        if before_sequence:
            # 游标分页：获取指定序号之前的消息
            messages = await message_repo.find_by_user_before_sequence(
                user_id=user_id, before_sequence=before_sequence, limit=limit
            )
        else:
            # 首次加载：获取最新消息
            messages = await message_repo.find_latest_by_user(
                user_id=user_id, limit=limit
            )

        # 返回正序（最早的在前）
        messages.reverse()
        return [MessageVO.from_entity(m) for m in messages]

    @staticmethod
    async def get_generating_message(user_id: int) -> Optional[MessageVO]:
        """
        获取正在生成中的消息（用于页面刷新时检测）

        参考: behavior-model.md#2.4 流式响应重连

        Args:
            user_id: 用户ID

        Returns:
            Optional[MessageVO]: 生成中的消息或 None
        """
        message = await message_repo.find_generating_message(user_id)
        if message:
            return MessageVO.from_entity(message)
        return None


class AgentService:
    """
    Agent 执行服务

    参考: behavior-model.md#2.2 执行LangGraph Agent（B_CHAT_002）
    """

    @staticmethod
    async def execute(
        user_id: int, thread_id: str, request_id: str, user_message: str
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        执行 LangGraph Agent

        参考: behavior-model.md#2.2
        参考: spec.md US2场景10 - Agent失败时不入库消息

        消息入库时机（符合data-model.md#2.2语义）：
        - 用户消息：Agent执行成功后入库，created_time为Agent接收消息时间
        - assistant消息：首个token接收时入库，created_time为首个token时间

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            request_id: 请求ID
            user_message: 用户消息

        Yields:
            StreamChunk: 流式响应块
        """
        execution_uuid = str(uuid.uuid4())
        start_time = timezone.now()
        stop_event = register_generation(request_id)

        # 1. 创建执行记录（PostgreSQL，用于监控）
        execution = LangGraphExecution(
            execution_uuid=execution_uuid,
            request_id=request_id,
            user_id=user_id,
            thread_id=thread_id,
            graph_name="react_agent",
            status=LangGraphExecution.STATUS_PENDING,
            start_time=start_time,
            input_data={"message": user_message},
        )
        await execution_repo.create(execution)

        # 变量初始化
        full_response = ""
        total_prompt_tokens = 0
        total_completion_tokens = 0
        user_msg: Optional[Message] = None
        assistant_msg: Optional[Message] = None
        interrupted = False
        first_token_received = False
        first_token_time: Optional[datetime] = None
        max_seq: Optional[int] = None

        try:
            # 2. 初始化 Langfuse 追踪（可选，失败不影响主流程）
            # Langfuse v3.x 通过环境变量配置：
            # LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
            langfuse_handler = None
            try:
                if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
                    # v3.x CallbackHandler 从环境变量读取 secret_key 和 host
                    # 确保环境变量已设置（Django settings 可能通过 dotenv 加载）
                    import os

                    os.environ.setdefault(
                        "LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY
                    )
                    os.environ.setdefault(
                        "LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY
                    )
                    os.environ.setdefault("LANGFUSE_HOST", settings.LANGFUSE_HOST)

                    langfuse_handler = LangfuseCallbackHandler()
                    logger.info(f"Langfuse handler initialized for request {request_id}")
            except Exception as e:
                logger.warning(f"Failed to initialize Langfuse: {e}")

            # 3. 获取当前最大消息序号（延迟到首个token时再入库）
            max_seq = await message_repo.get_max_sequence(user_id)

            # 4. 更新执行状态为运行中
            execution.status = LangGraphExecution.STATUS_RUNNING
            await execution_repo.update(execution)

            # 5. 创建 Agent 并执行（使用上下文管理器确保 checkpointer 正确关闭）
            callbacks = [langfuse_handler] if langfuse_handler else None
            config = get_agent_config(user_id, callbacks)

            # 只需传当前消息，历史由 Checkpoint 自动注入
            input_message = {"messages": [HumanMessage(content=user_message)]}

            # 6. 流式执行（带超时）
            # 使用上下文管理器创建 agent，确保 checkpointer 在整个流程中保持打开
            async with create_chat_agent() as agent:
                try:
                    async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                        async for event in agent.astream_events(
                            input_message, config=config, version="v2"
                        ):
                            # 提取事件类型
                            event_type = event.get("event", "unknown")

                            # 检查是否收到停止信号
                            if stop_event.is_set():
                                interrupted = True
                                logger.info(f"Generation stopped by user: {request_id}")
                                break

                            # 处理流式内容
                            if event["event"] == "on_chat_model_stream":
                                chunk = event["data"]["chunk"]
                                if hasattr(chunk, "content") and chunk.content:
                                    # 首个token时入库用户消息和assistant消息
                                    if not first_token_received:
                                        first_token_received = True
                                        first_token_time = timezone.now()

                                        # 入库用户消息（created_time为Agent接收消息时间）
                                        user_msg = Message(
                                            message_uuid=str(uuid.uuid4()),
                                            user_id=user_id,
                                            role=Message.ROLE_USER,
                                            content=user_message,
                                            request_id=request_id,
                                            sequence=max_seq + 1,
                                            status=Message.STATUS_NORMAL,
                                            created_time=start_time,
                                        )
                                        await message_repo.create(user_msg)

                                        # 入库assistant消息（created_time为首个token时间）
                                        assistant_msg = Message(
                                            message_uuid=str(uuid.uuid4()),
                                            user_id=user_id,
                                            role=Message.ROLE_ASSISTANT,
                                            content="",
                                            request_id=request_id,
                                            sequence=max_seq + 2,
                                            status=Message.STATUS_GENERATING,
                                            model_name=_get_language_model_name(),
                                            created_time=first_token_time,
                                        )
                                        await message_repo.create(assistant_msg)

                                    full_response += chunk.content
                                    # 首个 chunk 返回 request_id，用于前端停止/继续生成
                                    chunk_data = StreamChunk(
                                        type="content",
                                        content=chunk.content,
                                        message_id=assistant_msg.message_id if assistant_msg else None,
                                    )
                                    if len(full_response) == len(chunk.content):
                                        # 这是首个 chunk，包含 request_id
                                        chunk_data.request_id = request_id
                                    yield chunk_data

                            # Token 统计 (LangGraph 使用 on_chat_model_end 事件)
                            elif event_type == "on_chat_model_end":
                                data = event.get("data", {})
                                if "output" in data:
                                    output = data["output"]
                                    # 优先从 usage_metadata 获取（需要 ChatOpenAI 配置 stream_usage=True）
                                    if hasattr(output, "usage_metadata") and output.usage_metadata:
                                        usage = output.usage_metadata
                                        total_prompt_tokens += usage.get("input_tokens", 0)
                                        total_completion_tokens += usage.get("output_tokens", 0)
                                    # 备用：从 response_metadata 获取（某些API可能用这个字段）
                                    elif hasattr(output, "response_metadata") and output.response_metadata:
                                        meta = output.response_metadata
                                        if "token_usage" in meta:
                                            usage = meta["token_usage"]
                                            total_prompt_tokens += usage.get("prompt_tokens", 0)
                                            total_completion_tokens += usage.get("completion_tokens", 0)

                except asyncio.TimeoutError:
                    logger.error(f"Agent execution timeout: {request_id}")
                    raise LLMTimeoutError("AI响应超时，请稍后重试")

                # 7. 处理完成/中断状态
                end_time = timezone.now()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

                # 如果没有收到任何token就中断/完成，不入库消息
                if not first_token_received:
                    execution.status = LangGraphExecution.STATUS_FAILED
                    execution.error_type = "NoTokenReceived"
                    execution.error_message = "未收到任何响应"
                    execution.end_time = end_time
                    execution.duration_ms = duration_ms
                    await execution_repo.update(execution)
                    raise LLMInvalidResponseError("AI未返回任何响应，请重试")

                if interrupted:
                    # 中断处理：消息已入库，更新状态
                    full_response += "[已中断]"
                    assistant_msg.content = full_response
                    assistant_msg.status = Message.STATUS_INTERRUPTED
                    assistant_msg.response_time_ms = duration_ms
                    assistant_msg.prompt_tokens = total_prompt_tokens
                    assistant_msg.completion_tokens = total_completion_tokens
                    await message_repo.update(assistant_msg)

                    execution.status = "interrupted"
                    execution.end_time = end_time
                    execution.duration_ms = duration_ms
                    await execution_repo.update(execution)

                    yield StreamChunk(
                        type="interrupted",
                        content="[已中断]",
                        message_id=assistant_msg.message_id,
                    )
                else:
                    # 正常完成
                    assistant_msg.content = full_response
                    assistant_msg.status = Message.STATUS_NORMAL
                    assistant_msg.response_time_ms = duration_ms
                    assistant_msg.prompt_tokens = total_prompt_tokens
                    assistant_msg.completion_tokens = total_completion_tokens
                    await message_repo.update(assistant_msg)

                    # 更新执行记录
                    execution.status = LangGraphExecution.STATUS_COMPLETED
                    execution.end_time = end_time
                    execution.duration_ms = duration_ms
                    execution.output_data = {"response": full_response}
                    execution.total_prompt_tokens = total_prompt_tokens
                    execution.total_completion_tokens = total_completion_tokens
                    if langfuse_handler and hasattr(langfuse_handler, "trace_id"):
                        execution.langfuse_trace_id = langfuse_handler.trace_id
                    await execution_repo.update(execution)

                    # 更新用户统计
                    await user_repo.add_message_count(user_id, 2)
                    await user_repo.add_tokens(
                        user_id, total_prompt_tokens + total_completion_tokens
                    )

                    yield StreamChunk(
                        type="done", content="", message_id=assistant_msg.message_id
                    )

        except LLMException:
            # LLM 异常直接抛出，不入库消息（符合spec.md US2场景10）
            raise

        except Exception as e:
            logger.exception(f"Agent execution error: {request_id}")

            # 更新执行记录为失败
            execution.status = LangGraphExecution.STATUS_FAILED
            execution.error_type = type(e).__name__
            execution.error_message = str(e)
            execution.end_time = timezone.now()
            await execution_repo.update(execution)

            # 如果已创建 assistant 消息（首个token后失败），更新为失败状态
            if assistant_msg:
                assistant_msg.status = Message.STATUS_FAILED
                assistant_msg.content = full_response or ""
                await message_repo.update(assistant_msg)

            # 映射并抛出 LLM 异常
            raise map_llm_exception(e)

        finally:
            # 清理活跃会话记录
            unregister_generation(request_id)

            # 刷新 Langfuse 数据（确保 trace 被发送）
            if langfuse_handler and langfuse_handler.client:
                try:
                    langfuse_handler.client.flush()
                    logger.debug(f"Langfuse handler flushed for request {request_id}")
                except Exception as e:
                    logger.warning(f"Failed to flush Langfuse handler: {e}")

    @staticmethod
    async def resume(
        user_id: int, thread_id: str, request_id: str, message: Message
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        从中断处恢复生成

        参考: behavior-model.md#2.5 继续生成

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            request_id: 请求ID
            message: 被中断的消息

        Yields:
            StreamChunk: 流式响应块
        """
        stop_event = register_generation(request_id)
        existing_content = message.content.replace("[已中断]", "")
        full_response = existing_content

        try:
            # 创建 Agent（使用上下文管理器确保 checkpointer 正确关闭）
            config = get_agent_config(user_id)

            # 继续生成（发送"请继续"指令触发从 checkpoint 恢复）
            continue_message = HumanMessage(content="请继续")
            async with create_chat_agent() as agent:
                async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                    async for event in agent.astream_events(
                        {"messages": [continue_message]}, config=config, version="v2"
                    ):
                        if stop_event.is_set():
                            # 再次中断
                            full_response += "[已中断]"
                            await message_repo.update_content_and_status(
                                message.message_id,
                                user_id,
                                full_response,
                                Message.STATUS_INTERRUPTED,
                            )
                            yield StreamChunk(
                                type="interrupted",
                                content="[已中断]",
                                message_id=message.message_id,
                            )
                            return

                        if event["event"] == "on_chat_model_stream":
                            chunk = event["data"]["chunk"]
                            if hasattr(chunk, "content") and chunk.content:
                                full_response += chunk.content
                                yield StreamChunk(
                                    type="content",
                                    content=chunk.content,
                                    message_id=message.message_id,
                                )

                # 完成
                await message_repo.update_content_and_status(
                    message.message_id, user_id, full_response, Message.STATUS_NORMAL
                )
                yield StreamChunk(type="done", content="", message_id=message.message_id)

        except Exception as e:
            logger.exception(f"Resume generation error: {request_id}")
            await message_repo.update_status(
                message.message_id, user_id, Message.STATUS_FAILED
            )
            yield StreamChunk(type="error", content="恢复生成失败，请重试")

        finally:
            unregister_generation(request_id)
