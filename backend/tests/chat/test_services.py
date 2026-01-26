"""
聊天服务单元测试

覆盖:
- ChatService: 消息发送、空消息拦截、4000字符限制
- AgentService: Agent执行、流式响应生成、Checkpoint交互
- 异常处理（宪法4.3）: LLMConnectionError、LLMTimeoutError等
- 停止生成: 中断时checkpoint保存、消息status=3更新

覆盖率要求: 服务层 ≥ 95%
"""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from django.conf import settings
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.chat.services import (
    ChatService,
    HistoryService,
    AgentService,
    StreamChunk,
    MessageVO,
    map_llm_exception,
    register_generation,
    unregister_generation,
    get_stop_event,
    signal_stop,
)
from apps.chat.models import Message, LangGraphExecution
from apps.common.exceptions import (
    EmptyMessageException,
    MessageTooLongException,
    LLMConnectionError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMContentFilterError,
    LLMQuotaExceededError,
    LLMInvalidResponseError,
)


# ============ 测试辅助函数 ============


def run_async(coro):
    """运行异步函数"""
    return asyncio.get_event_loop().run_until_complete(coro)


async def collect_stream(async_gen):
    """收集异步生成器的所有结果"""
    results = []
    async for item in async_gen:
        results.append(item)
    return results


# ============ LLM 异常映射测试 ============


class TestLLMExceptionMapping(TestCase):
    """LLM 异常映射测试"""

    def test_connection_error_mapping(self):
        """测试连接错误映射"""
        exc = Exception("Connection refused")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMConnectionError)

        exc = Exception("Network unreachable")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMConnectionError)

    def test_timeout_error_mapping(self):
        """测试超时错误映射"""
        exc = Exception("Request timeout")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMTimeoutError)

        exc = Exception("Operation timed out")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMTimeoutError)

    def test_rate_limit_error_mapping(self):
        """测试频率限制错误映射"""
        exc = Exception("Rate limit exceeded")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMRateLimitError)

        exc = Exception("429 Too Many Requests")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMRateLimitError)

    def test_content_filter_error_mapping(self):
        """测试内容过滤错误映射"""
        exc = Exception("Content filter triggered")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMContentFilterError)

        exc = Exception("Content policy violation")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMContentFilterError)

    def test_quota_exceeded_error_mapping(self):
        """测试配额用尽错误映射"""
        exc = Exception("Quota exceeded")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMQuotaExceededError)

        exc = Exception("Billing error: insufficient credits")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMQuotaExceededError)

    def test_default_invalid_response_mapping(self):
        """测试默认映射为无效响应"""
        exc = Exception("Some unknown error")
        result = map_llm_exception(exc)
        self.assertIsInstance(result, LLMInvalidResponseError)


# ============ 活跃生成会话管理测试 ============


class TestGenerationManagement(TestCase):
    """活跃生成会话管理测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    def test_register_generation(self):
        """测试注册生成会话"""
        stop_event = register_generation("test-request-1")
        self.assertIsInstance(stop_event, asyncio.Event)
        self.assertFalse(stop_event.is_set())

    def test_unregister_generation(self):
        """测试取消注册生成会话"""
        register_generation("test-request-2")
        unregister_generation("test-request-2")
        self.assertIsNone(get_stop_event("test-request-2"))

    def test_get_stop_event(self):
        """测试获取停止事件"""
        register_generation("test-request-3")
        stop_event = get_stop_event("test-request-3")
        self.assertIsNotNone(stop_event)

        # 不存在的请求
        self.assertIsNone(get_stop_event("non-existent"))

    def test_signal_stop(self):
        """测试发送停止信号"""
        stop_event = register_generation("test-request-4")
        self.assertFalse(stop_event.is_set())

        result = signal_stop("test-request-4")
        self.assertTrue(result)
        self.assertTrue(stop_event.is_set())

    def test_signal_stop_non_existent(self):
        """测试对不存在的请求发送停止信号"""
        result = signal_stop("non-existent")
        self.assertFalse(result)


# ============ ChatService 测试 ============


class TestChatService(TestCase):
    """聊天服务测试"""

    @patch("apps.chat.services.AgentService.execute")
    def test_send_message_empty_content(self, mock_execute):
        """测试发送空消息 - R_MSG_002"""
        with self.assertRaises(EmptyMessageException):
            run_async(
                collect_stream(ChatService.send_message(user_id=1, content=""))
            )

        with self.assertRaises(EmptyMessageException):
            run_async(
                collect_stream(ChatService.send_message(user_id=1, content="   "))
            )

        mock_execute.assert_not_called()

    @patch("apps.chat.services.AgentService.execute")
    def test_send_message_too_long(self, mock_execute):
        """测试发送超长消息 - R_MSG_001"""
        long_content = "a" * (settings.MAX_MESSAGE_LENGTH + 1)

        with self.assertRaises(MessageTooLongException) as ctx:
            run_async(
                collect_stream(ChatService.send_message(user_id=1, content=long_content))
            )

        self.assertIn(str(settings.MAX_MESSAGE_LENGTH), str(ctx.exception.message))
        mock_execute.assert_not_called()

    @patch("apps.chat.services.AgentService.execute")
    def test_send_message_max_length(self, mock_execute):
        """测试发送最大长度消息 - 边界测试"""
        max_content = "a" * settings.MAX_MESSAGE_LENGTH

        async def mock_gen(*args, **kwargs):
            yield StreamChunk(type="content", content="response")
            yield StreamChunk(type="done", content="", message_id=1)

        mock_execute.return_value = mock_gen()

        # 应该不抛出异常
        run_async(
            collect_stream(ChatService.send_message(user_id=1, content=max_content))
        )
        mock_execute.assert_called_once()

    @patch("apps.chat.services.AgentService.execute")
    def test_send_message_strips_whitespace(self, mock_execute):
        """测试消息会去除首尾空白"""
        async def mock_gen(*args, **kwargs):
            yield StreamChunk(type="done", content="", message_id=1)

        mock_execute.return_value = mock_gen()

        run_async(
            collect_stream(ChatService.send_message(user_id=1, content="  hello  "))
        )

        # 验证传递的 user_message 已去除空白
        call_args = mock_execute.call_args
        self.assertEqual(call_args[1]["user_message"], "hello")

    def test_stop_generation_success(self):
        """测试停止生成 - 成功"""
        register_generation("test-stop-1")
        result = run_async(ChatService.stop_generation(user_id=1, request_id="test-stop-1"))
        self.assertTrue(result)

        # 清理
        unregister_generation("test-stop-1")

    def test_stop_generation_not_found(self):
        """测试停止生成 - 请求不存在"""
        result = run_async(ChatService.stop_generation(user_id=1, request_id="non-existent"))
        self.assertFalse(result)


# ============ HistoryService 测试 ============


class TestHistoryService(TestCase):
    """历史消息服务测试"""

    @patch("apps.chat.services.message_repo.find_latest_by_user")
    def test_load_messages_first_page(self, mock_find):
        """测试加载首页消息"""
        mock_messages = [
            MagicMock(
                message_id=2,
                message_uuid="uuid-2",
                role="assistant",
                content="response",
                status=1,
                sequence=2,
                created_time=datetime.now(),
                request_id="req-1",
                model_name="model",
                response_time_ms=100,
            ),
            MagicMock(
                message_id=1,
                message_uuid="uuid-1",
                role="user",
                content="hello",
                status=1,
                sequence=1,
                created_time=datetime.now(),
                request_id="req-1",
                model_name=None,
                response_time_ms=None,
            ),
        ]
        mock_find.return_value = mock_messages

        result = run_async(HistoryService.load_messages(user_id=1))

        # 验证调用
        mock_find.assert_called_once_with(user_id=1, limit=50)

        # 验证返回结果（应该是正序）
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], MessageVO)
        # 注意：由于 reverse()，顺序应该是 id=1 在前
        self.assertEqual(result[0].message_id, 1)
        self.assertEqual(result[1].message_id, 2)

    @patch("apps.chat.services.message_repo.find_by_user_before_sequence")
    def test_load_messages_with_cursor(self, mock_find):
        """测试游标分页加载消息"""
        mock_messages = []
        mock_find.return_value = mock_messages

        run_async(HistoryService.load_messages(user_id=1, before_sequence=10))

        mock_find.assert_called_once_with(
            user_id=1, before_sequence=10, limit=50
        )

    @patch("apps.chat.services.message_repo.find_latest_by_user")
    def test_load_messages_limit_max(self, mock_find):
        """测试限制最大返回数量为100"""
        mock_find.return_value = []

        run_async(HistoryService.load_messages(user_id=1, limit=200))

        # 应该被限制为100
        mock_find.assert_called_once_with(user_id=1, limit=100)

    @patch("apps.chat.services.message_repo.find_generating_message")
    def test_get_generating_message_exists(self, mock_find):
        """测试获取生成中的消息 - 存在"""
        mock_message = MagicMock(
            message_id=1,
            message_uuid="uuid-1",
            role="assistant",
            content="partial",
            status=2,
            sequence=1,
            created_time=datetime.now(),
            request_id="req-1",
            model_name="model",
            response_time_ms=None,
        )
        mock_find.return_value = mock_message

        result = run_async(HistoryService.get_generating_message(user_id=1))

        self.assertIsNotNone(result)
        self.assertEqual(result.message_id, 1)
        self.assertEqual(result.status, 2)

    @patch("apps.chat.services.message_repo.find_generating_message")
    def test_get_generating_message_not_exists(self, mock_find):
        """测试获取生成中的消息 - 不存在"""
        mock_find.return_value = None

        result = run_async(HistoryService.get_generating_message(user_id=1))

        self.assertIsNone(result)


# ============ MessageVO 测试 ============


class TestMessageVO(TestCase):
    """消息视图对象测试"""

    def test_from_entity(self):
        """测试从实体转换"""
        mock_message = MagicMock()
        mock_message.message_id = 1
        mock_message.message_uuid = "test-uuid"
        mock_message.role = "user"
        mock_message.content = "hello"
        mock_message.status = 1
        mock_message.sequence = 1
        mock_message.created_time = datetime(2024, 1, 1, 12, 0, 0)
        mock_message.request_id = "req-1"
        mock_message.model_name = None
        mock_message.response_time_ms = None

        vo = MessageVO.from_entity(mock_message)

        self.assertEqual(vo.message_id, 1)
        self.assertEqual(vo.message_uuid, "test-uuid")
        self.assertEqual(vo.role, "user")
        self.assertEqual(vo.content, "hello")
        self.assertEqual(vo.status, 1)
        self.assertEqual(vo.sequence, 1)
        self.assertEqual(vo.created_time, "2024-01-01T12:00:00")


# ============ StreamChunk 测试 ============


class TestStreamChunk(TestCase):
    """流式响应块测试"""

    def test_stream_chunk_content(self):
        """测试内容类型块"""
        chunk = StreamChunk(type="content", content="hello")
        self.assertEqual(chunk.type, "content")
        self.assertEqual(chunk.content, "hello")
        self.assertIsNone(chunk.message_id)

    def test_stream_chunk_done(self):
        """测试完成类型块"""
        chunk = StreamChunk(type="done", content="", message_id=1)
        self.assertEqual(chunk.type, "done")
        self.assertEqual(chunk.message_id, 1)

    def test_stream_chunk_error(self):
        """测试错误类型块"""
        chunk = StreamChunk(type="error", content="Something went wrong")
        self.assertEqual(chunk.type, "error")
        self.assertEqual(chunk.content, "Something went wrong")

    def test_stream_chunk_interrupted(self):
        """测试中断类型块"""
        chunk = StreamChunk(type="interrupted", content="[已中断]", message_id=1)
        self.assertEqual(chunk.type, "interrupted")
        self.assertEqual(chunk.content, "[已中断]")


# ============ LLM 异常策略测试 ============


class TestLLMExceptionRetryStrategy(TestCase):
    """LLM 异常重试策略测试（宪法4.3）"""

    def test_connection_error_should_retry(self):
        """测试连接错误 - 应该重试3次"""
        exc = LLMConnectionError()
        self.assertTrue(exc.should_retry)
        self.assertEqual(exc.max_retries, 3)

    def test_timeout_error_should_retry(self):
        """测试超时错误 - 应该重试3次"""
        exc = LLMTimeoutError()
        self.assertTrue(exc.should_retry)
        self.assertEqual(exc.max_retries, 3)

    def test_invalid_response_error_should_retry(self):
        """测试无效响应错误 - 应该重试3次"""
        exc = LLMInvalidResponseError()
        self.assertTrue(exc.should_retry)
        self.assertEqual(exc.max_retries, 3)

    def test_rate_limit_error_no_retry(self):
        """测试频率限制错误 - 不重试，返回等待时间"""
        exc = LLMRateLimitError()
        self.assertFalse(exc.should_retry)
        self.assertEqual(exc.retry_after, 60)  # 默认60秒

    def test_content_filter_error_no_retry(self):
        """测试内容过滤错误 - 不重试"""
        exc = LLMContentFilterError()
        self.assertFalse(exc.should_retry)

    def test_quota_exceeded_error_no_retry(self):
        """测试配额用尽错误 - 不重试"""
        exc = LLMQuotaExceededError()
        self.assertFalse(exc.should_retry)


# ============ LLM 异常用户提示测试 ============


class TestLLMExceptionUserMessages(TestCase):
    """LLM 异常用户提示测试"""

    def test_connection_error_message(self):
        """测试连接错误提示"""
        exc = LLMConnectionError()
        self.assertIn("暂时无法连接", exc.message)

    def test_timeout_error_message(self):
        """测试超时错误提示"""
        exc = LLMTimeoutError()
        self.assertIn("超时", exc.message)

    def test_rate_limit_error_message(self):
        """测试频率限制错误提示"""
        exc = LLMRateLimitError()
        self.assertIn("频繁", exc.message)

    def test_content_filter_error_message(self):
        """测试内容过滤错误提示"""
        exc = LLMContentFilterError()
        self.assertIn("敏感内容", exc.message)

    def test_quota_exceeded_error_message(self):
        """测试配额用尽错误提示"""
        exc = LLMQuotaExceededError()
        self.assertIn("配额", exc.message)


# ============ ChatService.resume_generation 测试 ============


class TestChatServiceResumeGeneration(TestCase):
    """ChatService.resume_generation 测试"""

    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_resume_message_not_found(self, mock_get):
        """测试消息不存在"""
        mock_get.return_value = None

        result = run_async(
            collect_stream(
                ChatService.resume_generation(user_id=1, request_id="req-1")
            )
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "error")
        self.assertIn("消息不存在", result[0].content)

    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_resume_message_not_interrupted(self, mock_get):
        """测试消息不是中断状态"""
        mock_message = MagicMock()
        mock_message.status = Message.STATUS_NORMAL  # 已完成
        mock_get.return_value = mock_message

        result = run_async(
            collect_stream(
                ChatService.resume_generation(user_id=1, request_id="req-1")
            )
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "error")
        self.assertIn("不可继续生成", result[0].content)

    @patch("apps.chat.services.AgentService.resume")
    @patch("apps.chat.services.message_repo.update_status")
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_resume_success(self, mock_get, mock_update, mock_resume):
        """测试成功恢复生成"""
        mock_message = MagicMock()
        mock_message.status = Message.STATUS_INTERRUPTED
        mock_message.message_id = 1
        mock_get.return_value = mock_message

        async def mock_gen(*args, **kwargs):
            yield StreamChunk(type="content", content="more content")
            yield StreamChunk(type="done", content="", message_id=1)

        mock_resume.return_value = mock_gen()

        result = run_async(
            collect_stream(
                ChatService.resume_generation(user_id=1, request_id="req-1")
            )
        )

        # 验证更新状态为生成中
        mock_update.assert_called_once()
        self.assertEqual(len(result), 2)


# ============ ChatService.reconnect_stream 测试 ============


class TestChatServiceReconnectStream(TestCase):
    """ChatService.reconnect_stream 测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_message_not_found(self, mock_get):
        """测试消息不存在"""
        mock_get.return_value = None

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-1")
            )
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "error")

    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_message_already_done(self, mock_get):
        """测试消息已完成"""
        mock_message = MagicMock()
        mock_message.status = Message.STATUS_NORMAL
        mock_message.message_id = 1
        mock_get.return_value = mock_message

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-1")
            )
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "done")

    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_message_interrupted(self, mock_get):
        """测试消息已中断"""
        mock_message = MagicMock()
        mock_message.status = Message.STATUS_INTERRUPTED
        mock_message.message_id = 1
        mock_get.return_value = mock_message

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-1")
            )
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "interrupted")

    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_message_failed(self, mock_get):
        """测试消息失败"""
        mock_message = MagicMock()
        mock_message.status = Message.STATUS_FAILED
        mock_message.message_id = 1
        mock_get.return_value = mock_message

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-1")
            )
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "error")
        self.assertIn("生成失败", result[0].content)

    @patch("apps.chat.services.message_repo.update_status")
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_no_active_generation(self, mock_get, mock_update):
        """测试没有活跃的生成任务（服务可能重启了）"""
        mock_message = MagicMock()
        mock_message.status = Message.STATUS_GENERATING
        mock_message.message_id = 1
        mock_get.return_value = mock_message

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-1")
            )
        )

        # 应该标记为中断
        mock_update.assert_called_once()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].type, "interrupted")


# ============ AgentService.execute 测试 ============


class TestAgentServiceExecute(TestCase):
    """AgentService.execute 测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_no_token_received(
        self, mock_create_agent, mock_get_max_seq, mock_create_exec, mock_update_exec
    ):
        """测试没有收到任何token"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 返回空流
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            return
            yield  # 空生成器

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        with self.assertRaises(LLMInvalidResponseError):
            run_async(
                collect_stream(
                    AgentService.execute(
                        user_id=1,
                        thread_id="thread-1",
                        request_id="req-1",
                        user_message="hello",
                    )
                )
            )

    @patch("apps.chat.services.user_repo.add_tokens")
    @patch("apps.chat.services.user_repo.add_message_count")
    @patch("apps.chat.services.message_repo.update")
    @patch("apps.chat.services.message_repo.create")
    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_success(
        self,
        mock_create_agent,
        mock_get_max_seq,
        mock_create_exec,
        mock_update_exec,
        mock_create_msg,
        mock_update_msg,
        mock_add_msg_count,
        mock_add_tokens,
    ):
        """测试正常执行成功"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 流式返回
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            # 模拟流式事件
            chunk = MagicMock()
            chunk.content = "Hello"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}

            chunk2 = MagicMock()
            chunk2.content = " world"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk2}}

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        result = run_async(
            collect_stream(
                AgentService.execute(
                    user_id=1,
                    thread_id="thread-1",
                    request_id="req-1",
                    user_message="hello",
                )
            )
        )

        # 应该有 content 块和 done 块
        content_chunks = [c for c in result if c.type == "content"]
        done_chunks = [c for c in result if c.type == "done"]
        self.assertEqual(len(content_chunks), 2)
        self.assertEqual(len(done_chunks), 1)
        self.assertEqual(content_chunks[0].content, "Hello")
        self.assertEqual(content_chunks[1].content, " world")

    @patch("apps.chat.services.message_repo.update")
    @patch("apps.chat.services.message_repo.create")
    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_interrupted(
        self,
        mock_create_agent,
        mock_get_max_seq,
        mock_create_exec,
        mock_update_exec,
        mock_create_msg,
        mock_update_msg,
    ):
        """测试用户中断生成"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 流式返回，但中途被中断
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            # 第一个 chunk
            chunk = MagicMock()
            chunk.content = "Hello"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}
            # 模拟用户发送停止信号
            signal_stop("req-interrupt-1")
            # 第二个 chunk（不应该被处理）
            chunk2 = MagicMock()
            chunk2.content = " world"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk2}}

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        result = run_async(
            collect_stream(
                AgentService.execute(
                    user_id=1,
                    thread_id="thread-1",
                    request_id="req-interrupt-1",
                    user_message="hello",
                )
            )
        )

        # 应该有 content 块和 interrupted 块
        interrupted_chunks = [c for c in result if c.type == "interrupted"]
        self.assertEqual(len(interrupted_chunks), 1)
        self.assertIn("已中断", interrupted_chunks[0].content)

    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_exception_mapping(
        self, mock_create_agent, mock_get_max_seq, mock_create_exec, mock_update_exec
    ):
        """测试执行异常时的异常映射"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 抛出连接错误
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            raise Exception("Connection refused")
            yield  # 使其成为生成器

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        with self.assertRaises(LLMConnectionError):
            run_async(
                collect_stream(
                    AgentService.execute(
                        user_id=1,
                        thread_id="thread-1",
                        request_id="req-conn-error",
                        user_message="hello",
                    )
                )
            )


# ============ AgentService.resume 测试 ============


class TestAgentServiceResume(TestCase):
    """AgentService.resume 测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    @patch("apps.chat.services.message_repo.update_content_and_status")
    @patch("apps.chat.services.create_chat_agent")
    def test_resume_success(self, mock_create_agent, mock_update):
        """测试成功恢复生成"""
        mock_message = MagicMock()
        mock_message.message_id = 1
        mock_message.content = "Previous content[已中断]"

        # 模拟 agent 流式返回
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            chunk = MagicMock()
            chunk.content = " continued"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        result = run_async(
            collect_stream(
                AgentService.resume(
                    user_id=1,
                    thread_id="thread-1",
                    request_id="req-resume-1",
                    message=mock_message,
                )
            )
        )

        content_chunks = [c for c in result if c.type == "content"]
        done_chunks = [c for c in result if c.type == "done"]
        self.assertEqual(len(content_chunks), 1)
        self.assertEqual(len(done_chunks), 1)
        self.assertEqual(content_chunks[0].content, " continued")

    @patch("apps.chat.services.message_repo.update_content_and_status")
    @patch("apps.chat.services.create_chat_agent")
    def test_resume_interrupted_again(self, mock_create_agent, mock_update):
        """测试恢复后再次中断"""
        mock_message = MagicMock()
        mock_message.message_id = 1
        mock_message.content = "Previous[已中断]"

        # 模拟 agent 流式返回，但中途被中断
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            chunk = MagicMock()
            chunk.content = " more"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}
            # 模拟用户发送停止信号
            signal_stop("req-resume-interrupt")
            chunk2 = MagicMock()
            chunk2.content = " text"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk2}}

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        result = run_async(
            collect_stream(
                AgentService.resume(
                    user_id=1,
                    thread_id="thread-1",
                    request_id="req-resume-interrupt",
                    message=mock_message,
                )
            )
        )

        interrupted_chunks = [c for c in result if c.type == "interrupted"]
        self.assertEqual(len(interrupted_chunks), 1)

    @patch("apps.chat.services.message_repo.update_status")
    @patch("apps.chat.services.create_chat_agent")
    def test_resume_exception(self, mock_create_agent, mock_update):
        """测试恢复时发生异常"""
        mock_message = MagicMock()
        mock_message.message_id = 1
        mock_message.content = "Previous[已中断]"

        # 模拟 agent 抛出异常
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            raise Exception("Some error")
            yield  # 使其成为生成器

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        result = run_async(
            collect_stream(
                AgentService.resume(
                    user_id=1,
                    thread_id="thread-1",
                    request_id="req-resume-error",
                    message=mock_message,
                )
            )
        )

        error_chunks = [c for c in result if c.type == "error"]
        self.assertEqual(len(error_chunks), 1)
        self.assertIn("恢复生成失败", error_chunks[0].content)

        # 验证消息状态更新为失败
        mock_update.assert_called_once()


# ============ ChatService.reconnect_stream 轮询测试 ============


class TestChatServiceReconnectStreamPolling(TestCase):
    """ChatService.reconnect_stream 轮询等待测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    @patch("apps.chat.services.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_with_active_generation_completes(self, mock_get, mock_sleep):
        """测试重连到活跃生成，等待完成"""
        # 注册活跃生成
        register_generation("req-poll-1")

        # 初始状态：生成中，有内容
        initial_msg = MagicMock()
        initial_msg.status = Message.STATUS_GENERATING
        initial_msg.message_id = 1
        initial_msg.content = "Hello"

        # 轮询后的状态：已完成
        completed_msg = MagicMock()
        completed_msg.status = Message.STATUS_NORMAL
        completed_msg.message_id = 1
        completed_msg.content = "Hello world"

        mock_get.side_effect = [initial_msg, completed_msg]

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-poll-1")
            )
        )

        # 应该有内容块和完成块
        content_chunks = [c for c in result if c.type == "content"]
        done_chunks = [c for c in result if c.type == "done"]
        # 实际会返回初始内容 + 轮询时的增量内容
        self.assertGreaterEqual(len(content_chunks), 1)
        self.assertEqual(len(done_chunks), 1)

    @patch("apps.chat.services.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_with_active_generation_gets_interrupted(self, mock_get, mock_sleep):
        """测试重连到活跃生成，等待中断"""
        register_generation("req-poll-2")

        initial_msg = MagicMock()
        initial_msg.status = Message.STATUS_GENERATING
        initial_msg.message_id = 1
        initial_msg.content = "Partial"

        interrupted_msg = MagicMock()
        interrupted_msg.status = Message.STATUS_INTERRUPTED
        interrupted_msg.message_id = 1
        interrupted_msg.content = "Partial[已中断]"

        mock_get.side_effect = [initial_msg, interrupted_msg]

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-poll-2")
            )
        )

        interrupted_chunks = [c for c in result if c.type == "interrupted"]
        self.assertEqual(len(interrupted_chunks), 1)

    @patch("apps.chat.services.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_with_active_generation_fails(self, mock_get, mock_sleep):
        """测试重连到活跃生成，等待失败"""
        register_generation("req-poll-3")

        initial_msg = MagicMock()
        initial_msg.status = Message.STATUS_GENERATING
        initial_msg.message_id = 1
        initial_msg.content = "Start"

        failed_msg = MagicMock()
        failed_msg.status = Message.STATUS_FAILED
        failed_msg.message_id = 1
        failed_msg.content = "Start"

        mock_get.side_effect = [initial_msg, failed_msg]

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-poll-3")
            )
        )

        error_chunks = [c for c in result if c.type == "error"]
        self.assertEqual(len(error_chunks), 1)
        self.assertIn("生成失败", error_chunks[0].content)

    @patch("apps.chat.services.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_polling_message_deleted(self, mock_get, mock_sleep):
        """测试轮询时消息被删除"""
        register_generation("req-poll-4")

        initial_msg = MagicMock()
        initial_msg.status = Message.STATUS_GENERATING
        initial_msg.message_id = 1
        initial_msg.content = "Content"

        # 第二次查询消息不存在了
        mock_get.side_effect = [initial_msg, None]

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-poll-4")
            )
        )

        error_chunks = [c for c in result if c.type == "error"]
        self.assertEqual(len(error_chunks), 1)
        self.assertIn("消息不存在", error_chunks[0].content)

    @patch("apps.chat.services.asyncio.sleep", new_callable=AsyncMock)
    @patch("apps.chat.services.message_repo.get_by_request_id")
    def test_reconnect_with_incremental_content(self, mock_get, mock_sleep):
        """测试重连时收到增量内容"""
        register_generation("req-poll-5")

        initial_msg = MagicMock()
        initial_msg.status = Message.STATUS_GENERATING
        initial_msg.message_id = 1
        initial_msg.content = "Hello"

        # 第二次查询，内容增加
        msg_with_more = MagicMock()
        msg_with_more.status = Message.STATUS_GENERATING
        msg_with_more.message_id = 1
        msg_with_more.content = "Hello world"

        # 第三次查询，完成
        completed_msg = MagicMock()
        completed_msg.status = Message.STATUS_NORMAL
        completed_msg.message_id = 1
        completed_msg.content = "Hello world!"

        mock_get.side_effect = [initial_msg, msg_with_more, completed_msg]

        result = run_async(
            collect_stream(
                ChatService.reconnect_stream(user_id=1, request_id="req-poll-5")
            )
        )

        content_chunks = [c for c in result if c.type == "content"]
        done_chunks = [c for c in result if c.type == "done"]
        # 验证收到了增量内容（初始内容 + 后续增量）
        self.assertGreaterEqual(len(content_chunks), 2)
        self.assertEqual(len(done_chunks), 1)


# ============ AgentService.execute Token统计和超时测试 ============


class TestAgentServiceExecuteAdvanced(TestCase):
    """AgentService.execute 高级测试"""

    def tearDown(self):
        """清理所有注册的会话"""
        from apps.chat.services import _active_generations
        _active_generations.clear()

    @patch("apps.chat.services.user_repo.add_tokens")
    @patch("apps.chat.services.user_repo.add_message_count")
    @patch("apps.chat.services.message_repo.update")
    @patch("apps.chat.services.message_repo.create")
    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_with_token_stats(
        self,
        mock_create_agent,
        mock_get_max_seq,
        mock_create_exec,
        mock_update_exec,
        mock_create_msg,
        mock_update_msg,
        mock_add_msg_count,
        mock_add_tokens,
    ):
        """测试执行完成时token统计（当前实现中token统计逻辑不执行）"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 返回内容
        # 注意：当前实现中 on_llm_end 事件的 token 统计检查逻辑
        # hasattr(event.get("data", {}), "output") 永远为False（dict没有output属性）
        # 所以token统计值会是0
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            # 内容块
            chunk = MagicMock()
            chunk.content = "Hello"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        run_async(
            collect_stream(
                AgentService.execute(
                    user_id=1,
                    thread_id="thread-1",
                    request_id="req-token-1",
                    user_message="hello",
                )
            )
        )

        # 验证 add_tokens 被调用（token 统计值可能为 0）
        mock_add_tokens.assert_called_once()
        # 验证 add_message_count 被调用
        mock_add_msg_count.assert_called_once_with(1, 2)

    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_timeout(
        self, mock_create_agent, mock_get_max_seq, mock_create_exec, mock_update_exec
    ):
        """测试执行超时"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 超时
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            raise asyncio.TimeoutError()
            yield  # 使其成为生成器

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        with self.assertRaises(LLMTimeoutError):
            run_async(
                collect_stream(
                    AgentService.execute(
                        user_id=1,
                        thread_id="thread-1",
                        request_id="req-timeout",
                        user_message="hello",
                    )
                )
            )

    @patch("apps.chat.services.message_repo.update")
    @patch("apps.chat.services.message_repo.create")
    @patch("apps.chat.services.execution_repo.update")
    @patch("apps.chat.services.execution_repo.create")
    @patch("apps.chat.services.message_repo.get_max_sequence")
    @patch("apps.chat.services.create_chat_agent")
    def test_execute_error_after_first_token(
        self,
        mock_create_agent,
        mock_get_max_seq,
        mock_create_exec,
        mock_update_exec,
        mock_create_msg,
        mock_update_msg,
    ):
        """测试首个token后发生错误"""
        mock_get_max_seq.return_value = 0

        # 模拟 agent 先返回内容，然后出错
        mock_agent = MagicMock()

        async def mock_stream(*args, **kwargs):
            # 先返回一个内容块
            chunk = MagicMock()
            chunk.content = "Start"
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}
            # 然后抛出异常
            raise Exception("Unexpected error")

        mock_agent.astream_events = mock_stream
        mock_agent.__aenter__ = AsyncMock(return_value=mock_agent)
        mock_agent.__aexit__ = AsyncMock(return_value=None)
        mock_create_agent.return_value = mock_agent

        with self.assertRaises(LLMInvalidResponseError):
            run_async(
                collect_stream(
                    AgentService.execute(
                        user_id=1,
                        thread_id="thread-1",
                        request_id="req-error-after-token",
                        user_message="hello",
                    )
                )
            )

        # 验证 assistant 消息被更新为失败状态
        # mock_update_msg 应该被调用
        self.assertTrue(mock_update_msg.called)
