# 宪法代码示例参考文档

> **用途**: 本文档包含项目宪法中引用的所有代码实现示例
> **强制要求**: 编写相关功能时必须参考本文档的实现模式
> **关联文档**: [项目宪法](.specify/memory/constitution.md)

---

## 目录

1. [数据一致性保障示例](#1-数据一致性保障示例)
2. [缓存一致性策略示例](#2-缓存一致性策略示例)
3. [大模型服务异常处理示例](#3-大模型服务异常处理示例)
4. [后端测试代码示例](#4-后端测试代码示例)
5. [前端测试代码示例](#5-前端测试代码示例)
6. [测试数据管理示例](#6-测试数据管理示例)

---

## 1. 数据一致性保障示例

> **对应宪法条款**: 第一条 1.3.3 数据一致性保障机制 - **不可违背**

### 1.1 强一致性写入（同步方案）

适用场景：关键业务数据，必须保证 MySQL、ES、Redis 数据一致性

```python
# 核心原则：写操作原子性，失败必须回滚

from django.db import transaction
from elasticsearch.exceptions import ElasticsearchException
from redis.exceptions import RedisError

class MessageService:
    """消息服务 - 强一致性写入示例"""

    def create_message(self, conversation_id: str, content: str, role: str) -> Message:
        """
        创建消息 - 确保 MySQL、ES、Redis 数据一致性

        事务流程：
        1. 开启 MySQL 事务
        2. 写入 MySQL
        3. 同步写入 ES（失败则回滚 MySQL）
        4. 更新 Redis 缓存（失败则回滚 MySQL 和 ES）
        5. 全部成功后提交事务
        """
        es_doc_id = None

        try:
            with transaction.atomic():
                # 步骤1：写入 MySQL（事务保护）
                message = Message.objects.create(
                    conversation_id=conversation_id,
                    content=content,
                    role=role
                )

                # 步骤2：同步写入 Elasticsearch
                try:
                    es_result = self.es_client.index(
                        index='messages',
                        id=str(message.id),
                        document={
                            'id': str(message.id),
                            'conversation_id': conversation_id,
                            'content': content,
                            'role': role,
                            'created_at': message.created_at.isoformat()
                        },
                        refresh=True  # 立即可搜索
                    )
                    es_doc_id = es_result['_id']
                except ElasticsearchException as e:
                    # ES 写入失败，抛出异常触发 MySQL 回滚
                    logger.error(f"ES写入失败，回滚MySQL事务: {e}")
                    raise DataSyncError(f"Elasticsearch同步失败: {e}")

                # 步骤3：更新 Redis 缓存
                try:
                    cache_key = f"conversation:{conversation_id}:messages"
                    self.redis_client.delete(cache_key)  # 失效缓存

                    # 更新会话最后活跃时间
                    self.redis_client.hset(
                        f"conversation:{conversation_id}:meta",
                        "last_message_at",
                        message.created_at.isoformat()
                    )
                except RedisError as e:
                    # Redis 失败，需要回滚 ES 和 MySQL
                    logger.error(f"Redis更新失败，回滚ES和MySQL: {e}")
                    self._rollback_es_document('messages', es_doc_id)
                    raise DataSyncError(f"Redis同步失败: {e}")

                return message

        except DataSyncError:
            raise
        except Exception as e:
            # 清理可能已写入的 ES 文档
            if es_doc_id:
                self._rollback_es_document('messages', es_doc_id)
            logger.error(f"消息创建失败: {e}")
            raise

    def _rollback_es_document(self, index: str, doc_id: str) -> None:
        """回滚 ES 文档"""
        try:
            self.es_client.delete(index=index, id=doc_id, refresh=True)
            logger.info(f"ES文档回滚成功: {index}/{doc_id}")
        except ElasticsearchException as e:
            # 回滚失败，记录告警，后续通过补偿任务处理
            logger.critical(f"ES文档回滚失败，需人工处理: {index}/{doc_id}, 错误: {e}")
            self._create_compensation_task('es_delete', index, doc_id)
```

### 1.2 最终一致性写入（异步方案）

适用场景：对实时搜索要求不高的非关键数据

```python
from celery import shared_task

class MessageServiceAsync:
    """消息服务 - 最终一致性写入示例"""

    def create_message_async(self, conversation_id: str, content: str, role: str) -> Message:
        """
        创建消息 - MySQL 先写，ES/Redis 异步同步

        适用场景：对实时搜索要求不高的数据
        """
        with transaction.atomic():
            message = Message.objects.create(
                conversation_id=conversation_id,
                content=content,
                role=role
            )

            # 发送异步同步任务
            sync_message_to_es.delay(str(message.id))
            invalidate_conversation_cache.delay(conversation_id)

            return message


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_message_to_es(self, message_id: str):
    """
    异步同步消息到 ES

    重试策略：失败后等待60秒重试，最多重试3次
    """
    try:
        message = Message.objects.get(id=message_id)
        es_client.index(
            index='messages',
            id=message_id,
            document={...},
            refresh=True
        )
    except Message.DoesNotExist:
        logger.warning(f"消息不存在，跳过同步: {message_id}")
    except ElasticsearchException as e:
        logger.error(f"ES同步失败，准备重试: {message_id}, 错误: {e}")
        raise self.retry(exc=e)
```

### 1.3 数据一致性检查与修复

```python
@shared_task
def check_data_consistency():
    """
    定时数据一致性检查任务（建议每小时执行）

    检查内容：
    1. MySQL 和 ES 数据是否一致
    2. 是否存在孤立的 ES 文档
    3. 是否存在未同步到 ES 的 MySQL 记录
    """
    # 检查最近1小时的数据
    one_hour_ago = timezone.now() - timedelta(hours=1)

    # 获取 MySQL 中的消息 ID
    mysql_ids = set(
        Message.objects.filter(created_at__gte=one_hour_ago)
        .values_list('id', flat=True)
    )

    # 获取 ES 中的消息 ID
    es_result = es_client.search(
        index='messages',
        query={'range': {'created_at': {'gte': one_hour_ago.isoformat()}}},
        size=10000
    )
    es_ids = set(hit['_id'] for hit in es_result['hits']['hits'])

    # 找出不一致的数据
    missing_in_es = mysql_ids - es_ids  # MySQL有但ES没有
    orphan_in_es = es_ids - mysql_ids   # ES有但MySQL没有

    # 修复缺失的 ES 文档
    for msg_id in missing_in_es:
        sync_message_to_es.delay(str(msg_id))
        logger.warning(f"检测到ES缺失文档，已触发同步: {msg_id}")

    # 删除孤立的 ES 文档
    for doc_id in orphan_in_es:
        es_client.delete(index='messages', id=doc_id)
        logger.warning(f"检测到ES孤立文档，已删除: {doc_id}")

    return {
        'checked_count': len(mysql_ids),
        'missing_in_es': len(missing_in_es),
        'orphan_in_es': len(orphan_in_es)
    }
```

---

## 2. 缓存一致性策略示例

> **对应宪法条款**: 第一条 1.3.4 缓存一致性策略

采用 Cache-Aside Pattern（旁路缓存模式）

```python
class ConversationRepository:
    """会话数据仓库 - 缓存一致性实现"""

    CACHE_TTL = 300  # 5分钟

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """
        获取会话 - 缓存优先
        流程：缓存命中 -> 返回 / 缓存未命中 -> 查库 -> 写缓存 -> 返回
        """
        cache_key = f"conversation:{conversation_id}"

        # 1. 尝试从缓存读取
        cached = self.redis_client.get(cache_key)
        if cached:
            return Conversation.from_cache(json.loads(cached))

        # 2. 缓存未命中，查询数据库
        conversation = Conversation.objects.filter(id=conversation_id).first()
        if not conversation:
            return None

        # 3. 写入缓存
        self.redis_client.setex(
            cache_key,
            self.CACHE_TTL,
            json.dumps(conversation.to_cache_dict())
        )

        return conversation

    def update_conversation(self, conversation_id: str, **updates) -> Conversation:
        """
        更新会话 - 先更新数据库，再删除缓存

        重要：使用"删除缓存"而非"更新缓存"，避免并发写导致的数据不一致
        """
        with transaction.atomic():
            conversation = Conversation.objects.select_for_update().get(id=conversation_id)
            for key, value in updates.items():
                setattr(conversation, key, value)
            conversation.save()

            # 删除缓存，下次读取时重新加载
            cache_key = f"conversation:{conversation_id}"
            self.redis_client.delete(cache_key)

            return conversation

    def delete_conversation(self, conversation_id: str) -> None:
        """
        删除会话 - 级联删除所有相关数据
        """
        with transaction.atomic():
            # 1. 软删除 MySQL 数据
            Conversation.objects.filter(id=conversation_id).update(
                is_deleted=True,
                deleted_at=timezone.now()
            )
            Message.objects.filter(conversation_id=conversation_id).update(
                is_deleted=True,
                deleted_at=timezone.now()
            )

            # 2. 删除 ES 索引（同步执行确保一致性）
            try:
                self.es_client.delete_by_query(
                    index='messages',
                    query={'term': {'conversation_id': conversation_id}},
                    refresh=True
                )
            except ElasticsearchException as e:
                logger.error(f"ES删除失败: {e}")
                raise DataSyncError("删除会话失败，请稍后重试")

            # 3. 清理所有相关缓存
            cache_keys = [
                f"conversation:{conversation_id}",
                f"conversation:{conversation_id}:messages",
                f"conversation:{conversation_id}:meta",
            ]
            self.redis_client.delete(*cache_keys)
```

---

## 3. 大模型服务异常处理示例

> **对应宪法条款**: 第四条 4.3 大模型服务异常处理 - **不可违背**

### 3.1 异常类型定义

```python
"""
大模型 API 调用异常处理规范

说明：内容安全审核由统一大模型服务网关处理，本服务只需正确处理网关返回的错误响应
"""

class LLMException(Exception):
    """大模型服务异常基类"""
    pass

class LLMConnectionError(LLMException):
    """网络连接错误"""
    user_message = "AI 服务暂时无法连接，请稍后重试"

class LLMTimeoutError(LLMException):
    """请求超时"""
    user_message = "AI 响应超时，请重新发送消息"

class LLMRateLimitError(LLMException):
    """触发频率限制"""
    user_message = "请求过于频繁，请稍后再试"

class LLMContentFilterError(LLMException):
    """内容安全审核拦截（网关返回）"""
    user_message = "您的消息包含敏感内容，请修改后重试"

class LLMInvalidResponseError(LLMException):
    """响应格式异常"""
    user_message = "AI 响应异常，请重新发送消息"

class LLMQuotaExceededError(LLMException):
    """配额用尽"""
    user_message = "服务配额已用尽，请联系管理员"
```

### 3.2 客户端封装

```python
class LLMClientWrapper:
    """大模型客户端封装 - 统一异常处理"""

    def __init__(self, api_key: str, base_url: str, timeout: int = 60):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.timeout = timeout
        self.max_retries = 3
        self.retry_delay = 1  # 秒

    async def generate(
        self,
        messages: list[dict],
        model: str = "gpt-4",
        stream: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        生成 AI 响应（带重试和异常处理）

        参数:
            messages: 对话消息列表
            model: 模型名称
            stream: 是否流式响应

        返回:
            流式响应时返回异步生成器

        异常:
            LLMConnectionError: 网络连接失败
            LLMTimeoutError: 请求超时
            LLMRateLimitError: 触发频率限制
            LLMContentFilterError: 内容被安全审核拦截
            LLMInvalidResponseError: 响应格式异常
        """
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                response = await asyncio.wait_for(
                    self._make_request(messages, model, stream),
                    timeout=self.timeout
                )

                if stream:
                    async for chunk in self._handle_stream_response(response):
                        yield chunk
                else:
                    yield self._handle_response(response)
                return

            except asyncio.TimeoutError:
                last_exception = LLMTimeoutError("请求超时")
                logger.warning(f"大模型请求超时，第 {attempt + 1} 次尝试")

            except httpx.ConnectError as e:
                last_exception = LLMConnectionError(f"连接失败: {e}")
                logger.warning(f"大模型连接失败，第 {attempt + 1} 次尝试: {e}")

            except openai.RateLimitError as e:
                # 频率限制不重试，直接抛出
                raise LLMRateLimitError(str(e))

            except openai.BadRequestError as e:
                # 检查是否是内容安全拦截
                if self._is_content_filter_error(e):
                    raise LLMContentFilterError(
                        self._extract_filter_reason(e)
                    )
                raise LLMInvalidResponseError(str(e))

            # 重试前等待
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay * (attempt + 1))

        # 所有重试都失败
        raise last_exception

    def _is_content_filter_error(self, error: openai.BadRequestError) -> bool:
        """判断是否是内容安全拦截错误"""
        error_code = getattr(error, 'code', '')
        error_message = str(error).lower()

        filter_indicators = [
            'content_filter', 'content_policy', 'safety',
            'moderation', 'harmful', 'inappropriate',
        ]

        return any(indicator in error_message or indicator in error_code
                   for indicator in filter_indicators)

    def _extract_filter_reason(self, error: openai.BadRequestError) -> str:
        """提取安全拦截的具体原因"""
        try:
            error_body = error.body
            if isinstance(error_body, dict):
                return error_body.get('message', '内容审核未通过')
        except:
            pass
        return "内容审核未通过"

    async def _handle_stream_response(self, response) -> AsyncGenerator[str, None]:
        """处理流式响应"""
        try:
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"流式响应处理异常: {e}")
            raise LLMInvalidResponseError(f"响应处理失败: {e}")
```

### 3.3 业务层异常处理

```python
class ChatService:
    """聊天服务 - 大模型调用异常处理示例"""

    def __init__(self, llm_client: LLMClientWrapper):
        self.llm_client = llm_client

    async def send_message(
        self,
        conversation_id: str,
        user_message: str
    ) -> AsyncGenerator[dict, None]:
        """
        发送用户消息并获取 AI 响应

        返回格式:
            成功: {"type": "chunk", "content": "..."}
                  {"type": "complete", "message_id": "..."}
            失败: {"type": "error", "code": "...", "message": "..."}
        """
        try:
            messages = await self._build_messages(conversation_id, user_message)
            await self._save_user_message(conversation_id, user_message)

            full_response = []
            async for chunk in self.llm_client.generate(messages):
                full_response.append(chunk)
                yield {"type": "chunk", "content": chunk}

            assistant_message = await self._save_assistant_message(
                conversation_id, "".join(full_response)
            )
            yield {"type": "complete", "message_id": str(assistant_message.id)}

        except LLMContentFilterError as e:
            logger.info(f"用户消息被安全审核拦截: conversation={conversation_id}")
            yield {
                "type": "error",
                "code": "CONTENT_FILTERED",
                "message": e.user_message,
                "retry_allowed": True
            }

        except LLMRateLimitError as e:
            logger.warning(f"触发频率限制: conversation={conversation_id}")
            yield {
                "type": "error",
                "code": "RATE_LIMITED",
                "message": e.user_message,
                "retry_after": 60
            }

        except (LLMConnectionError, LLMTimeoutError) as e:
            logger.error(f"大模型服务异常: {type(e).__name__}, conversation={conversation_id}")
            yield {
                "type": "error",
                "code": "SERVICE_UNAVAILABLE",
                "message": e.user_message,
                "retry_allowed": True
            }

        except LLMInvalidResponseError as e:
            logger.error(f"大模型响应异常: {e}, conversation={conversation_id}")
            yield {
                "type": "error",
                "code": "INVALID_RESPONSE",
                "message": e.user_message,
                "retry_allowed": True
            }

        except Exception as e:
            logger.exception(f"未知异常: conversation={conversation_id}")
            yield {
                "type": "error",
                "code": "INTERNAL_ERROR",
                "message": "服务内部错误，请稍后重试",
                "retry_allowed": True
            }
```

---

## 4. 后端测试代码示例

> **对应宪法条款**: 第三条 3.3 后端测试标准

### 4.1 pytest 配置

```ini
# pytest.ini 配置
[pytest]
DJANGO_SETTINGS_MODULE = core.settings.test
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = --strict-markers -v --tb=short
markers =
    unit: 单元测试（隔离执行，速度快）
    integration: 集成测试（需要数据库）
    e2e: 端到端测试（完整系统）
    slow: 慢速测试（执行时间超过1秒）
```

### 4.2 单元测试示例

```python
@pytest.mark.unit
class TestChatService:
    """聊天服务单元测试"""

    @pytest.fixture
    def chat_service(self, mocker):
        """创建带 mock 依赖的聊天服务实例"""
        mock_llm = mocker.Mock(spec=LLMClient)
        mock_repo = mocker.Mock(spec=MessageRepository)
        return ChatService(llm_client=mock_llm, message_repo=mock_repo)

    async def test_generate_response_success(self, chat_service, mocker):
        """测试正常生成响应的情况"""
        conversation_id = uuid4()
        user_message = "你好，AI！"
        expected_response = "你好！有什么可以帮助你的吗？"

        chat_service.llm_client.generate.return_value = expected_response

        result = await chat_service.generate_response(
            conversation_id=conversation_id,
            message=user_message
        )

        assert result.content == expected_response
        chat_service.message_repo.save.assert_called_once()

    async def test_generate_response_llm_error(self, chat_service, mocker):
        """测试大模型调用失败的处理"""
        chat_service.llm_client.generate.side_effect = LLMConnectionError()

        with pytest.raises(ServiceUnavailableError):
            await chat_service.generate_response(uuid4(), "你好")
```

### 4.3 集成测试示例

```python
@pytest.mark.integration
class TestChatAPI:
    """聊天接口集成测试"""

    @pytest.fixture
    def api_client(self):
        return APIClient()

    @pytest.fixture
    def authenticated_client(self, api_client, create_user):
        user = create_user()
        api_client.force_authenticate(user=user)
        return api_client

    def test_create_conversation(self, authenticated_client):
        """测试创建新会话"""
        response = authenticated_client.post('/api/v1/conversations/', {
            'title': '测试会话'
        })

        assert response.status_code == 201
        assert 'id' in response.data['data']
        assert response.data['data']['title'] == '测试会话'
```

---

## 5. 前端测试代码示例

> **对应宪法条款**: 第三条 3.4 前端测试标准

### 5.1 Jest 配置

```javascript
// jest.config.js
module.exports = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/tests/setup.ts'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
  ],
};
```

### 5.2 组件测试示例

```typescript
describe('ChatMessage 组件', () => {
  it('正确渲染用户消息', () => {
    const message: Message = {
      id: '1',
      role: 'user',
      content: '你好，AI！',
      createdAt: new Date(),
    };

    render(<ChatMessage message={message} />);

    expect(screen.getByText('你好，AI！')).toBeInTheDocument();
    expect(screen.getByTestId('user-avatar')).toBeInTheDocument();
  });

  it('流式响应时显示加载指示器', () => {
    const message: Message = {
      id: '1',
      role: 'assistant',
      content: '正在思考...',
      createdAt: new Date(),
    };

    render(<ChatMessage message={message} isStreaming={true} />);

    expect(screen.getByTestId('streaming-indicator')).toBeInTheDocument();
  });

  it('点击重试按钮时调用回调函数', async () => {
    const onRetry = jest.fn();
    const message: Message = {
      id: '1',
      role: 'assistant',
      content: '响应出错',
      createdAt: new Date(),
    };

    render(<ChatMessage message={message} onRetry={onRetry} />);
    await userEvent.click(screen.getByRole('button', { name: /重试/i }));

    expect(onRetry).toHaveBeenCalledWith('1');
  });
});
```

### 5.3 Hooks 测试示例

```typescript
describe('useChatStream Hook', () => {
  it('组件挂载时建立 WebSocket 连接', () => {
    const mockWebSocket = jest.fn();
    global.WebSocket = mockWebSocket as any;

    renderHook(() => useChatStream('conv-123'));

    expect(mockWebSocket).toHaveBeenCalledWith(
      expect.stringContaining('/ws/chat/conv-123/')
    );
  });
});
```

---

## 6. 测试数据管理示例

> **对应宪法条款**: 第三条 3.5 测试数据管理

### 6.1 共享测试夹具

```python
# conftest.py
import pytest
from faker import Faker

fake = Faker('zh_CN')  # 使用中文数据

@pytest.fixture
def create_user(db):
    """工厂函数：创建测试用户"""
    def _create_user(
        email: str = None,
        username: str = None,
        is_active: bool = True
    ):
        from apps.users.models import User
        return User.objects.create_user(
            email=email or fake.email(),
            username=username or fake.user_name(),
            password='testpass123',
            is_active=is_active
        )
    return _create_user

@pytest.fixture
def create_conversation(db, create_user):
    """工厂函数：创建测试会话"""
    def _create_conversation(user=None, title=None):
        from apps.chat.models import Conversation
        return Conversation.objects.create(
            user=user or create_user(),
            title=title or fake.sentence(nb_words=4)
        )
    return _create_conversation
```

### 6.2 Factory Boy 复杂对象创建

```python
import factory
from factory.django import DjangoModelFactory

class MessageFactory(DjangoModelFactory):
    class Meta:
        model = Message

    conversation = factory.SubFactory(ConversationFactory)
    role = factory.Iterator(['user', 'assistant'])
    content = factory.Faker('paragraph', locale='zh_CN')
    created_at = factory.LazyFunction(timezone.now)
```

---

## 使用说明

1. **编写数据一致性相关代码时**，必须参考第 1、2 节的示例模式
2. **编写大模型调用相关代码时**，必须参考第 3 节的异常处理模式
3. **编写测试代码时**，必须参考第 4、5、6 节的测试模式
4. 所有示例代码仅供参考，实际实现需根据具体业务调整
5. 示例中的命名规范、错误处理方式是**强制要求**
