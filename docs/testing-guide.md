# LinChat 测试指南

> 本文档描述 LinChat 项目的测试体系、运行方式、编写规范和质量工具。
> 所有开发者在提交代码前必须确保相关测试通过。

---

## 目录

1. [测试概述](#1-测试概述)
2. [后端测试 (pytest)](#2-后端测试-pytest)
3. [前端单元测试 (Jest)](#3-前端单元测试-jest)
4. [端到端测试 (Playwright)](#4-端到端测试-playwright)
5. [测试模式与最佳实践](#5-测试模式与最佳实践)
6. [覆盖率报告](#6-覆盖率报告)
7. [代码质量工具](#7-代码质量工具)
8. [常用命令速查](#8-常用命令速查)

---

## 1. 测试概述

### 测试框架总览

| 层级 | 框架 | 配置文件 | 测试目录 |
|------|------|----------|----------|
| 后端单元/集成 | pytest + pytest-django + pytest-asyncio | `backend/pytest.ini` | `backend/tests/` |
| 前端单元 | Jest + @testing-library/react | `frontend/jest.config.js` | `frontend/src/**/__tests__/` + `frontend/tests/` |
| 端到端 | Playwright | `frontend/playwright.config.ts` | `frontend/tests/e2e/` |

### 覆盖率目标

| 范围 | 目标 |
|------|------|
| 总体覆盖率 | >= 80% |
| 服务层 (services) | >= 95% |
| 关键路径 (认证/数据一致性) | >= 95% |

### 测试规模

- **后端**: 89 个测试文件，约 1462 个测试函数
- **前端单元**: 14 个 `__tests__/` 文件 + 21 个 `tests/` 文件
- **端到端**: 4 个 Playwright 规范文件

---

## 2. 后端测试 (pytest)

### 2.1 配置

**pytest.ini** (`backend/pytest.ini`):

- `DJANGO_SETTINGS_MODULE = core.settings`
- `addopts = -v --tb=short --reuse-db` — 详细输出、简短 traceback、复用测试数据库
- `testpaths = tests`

**conftest.py** (`backend/conftest.py`):

全局 `pytest_configure()` 钩子在测试环境中禁用 DRF 限流，避免全量测试触发 429 错误。

**helpers.py** (`backend/tests/helpers.py`):

- `run_async(coro)` — 在同步测试中运行异步协程
- `collect_stream(async_gen)` — 收集异步生成器的所有结果到列表

### 2.2 测试模块

| 模块 | 目录 | 文件数 | 测试数(约) | 覆盖范围 |
|------|------|--------|------------|----------|
| chat | `tests/chat/` | 19 | ~200 | ChatService, SSE 流, 推理取消, 媒体, 模型路由, Agent |
| voice | `tests/voice/` | 16 | ~300 | WebSocket, ASR/TTS, 声纹, 设备, 语音管道, 聚合器 |
| users | `tests/users/` | 9 | ~180 | 登录/登出, 验证码, Token, SM3/SM4, 账户锁定 |
| memory | `tests/memory/` | 8 | ~120 | 记忆 CRUD, 向量搜索, Embedding, 定时总结 |
| graph | `tests/apps/graph/` | 7 | ~350 | Agent 工厂, SubAgent, HA 工具, 工具链 |
| models | `tests/models/` | 6 | ~100 | ModelConfig CRUD, SM4 加密密钥 |
| media | `tests/media/` | 4 | ~150 | 媒体上传/下载, 文档解析, RAG 向量分块 |
| performance | `tests/performance/` | 2 | ~12 | 性能基准测试 |
| common | `tests/common/` | 2 | ~60 | 通用工具, SSE 事件, Gateway |
| integration | `tests/integration/` | 1 | ~30 | SSE 异步集成测试 |
| context | `tests/context/` | 1 | ~40 | 上下文构建与裁剪 |

### 2.3 运行测试

**前置条件**: 必须激活虚拟环境且 PostgreSQL/Redis 服务运行中。

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 全部测试
pytest

# 按模块运行
pytest tests/chat/ -v
pytest tests/voice/ -v
pytest tests/apps/graph/ -v

# 按文件/类/方法运行
pytest tests/chat/test_services.py -v
pytest tests/chat/test_services.py::TestLLMExceptionMapping -v
pytest tests/chat/test_services.py::TestLLMExceptionMapping::test_connection_error_mapping -v

# 按关键字/标记过滤
pytest -k "test_connection" -v
pytest -m asyncio -v            # 仅异步测试（253+ 个）
pytest -m django_db -v          # 仅需数据库的测试
pytest -m benchmark -v          # 仅基准测试
```

### 2.4 异步测试

项目有 253+ 个 `@pytest.mark.asyncio` 标记的测试，覆盖 SSE 流式响应、WebSocket 通信、Agent 管道、Gateway 调用和 Redis 操作。

```python
@pytest.mark.asyncio
async def test_voice_pipeline_normal_flow():
    consumer = MagicMock()
    consumer._send_json = AsyncMock()

    with patch("apps.graph.services.AgentService") as mock_agent:
        mock_agent.execute = AsyncMock(return_value=async_gen_chunks())
        # ... 测试逻辑
```

同步上下文中使用 `tests.helpers.run_async()` 调用异步代码。

### 2.5 Fixtures

项目定义了 77 个 fixture，按类别划分：

| 类别 | 示例 | 说明 |
|------|------|------|
| 用户 | `test_user`, `admin_user` | 测试用户实例 |
| 消息 | `message`, `message_list` | 测试消息数据 |
| 模型配置 | `model_config`, `tool_model` | LLM 模型配置 |
| Redis Mock | `mock_redis`, `async_redis` | Redis 客户端 Mock |
| Gateway Mock | `mock_gateway` | Gateway HTTP Mock |
| 认证 | `authenticated_client` | 带认证的 API 客户端 |

### 2.6 测试标记 (Markers)

| 标记 | 数量 | 用途 |
|------|------|------|
| `@pytest.mark.asyncio` | 253+ | 异步测试 |
| `@pytest.mark.django_db` | 24 | 需真实数据库 |
| `@pytest.mark.benchmark` | 4 | 性能基准 |

---

## 3. 前端单元测试 (Jest)

### 3.1 配置

**jest.config.js** 关键项：

- `testEnvironment: 'jest-environment-jsdom'` — jsdom 模拟浏览器
- `moduleNameMapper: { '^@/(.*)$': '<rootDir>/src/$1' }` — `@/` 路径别名
- `testPathIgnorePatterns` — 排除 `node_modules/` 和 E2E 目录
- `coverageThreshold` — 全局 80% 门槛（branches/functions/lines/statements）

**jest.setup.js** 全局 Mock：

- `next/navigation` — 模拟 `useRouter`, `usePathname`, `useSearchParams`
- `window.matchMedia` — 模拟浏览器媒体查询 API

### 3.2 测试文件分布

**`src/` 下的 `__tests__/` 目录**（与源码同级）：

| 位置 | 覆盖范围 |
|------|----------|
| `src/app/login/__tests__/` | 登录页面 |
| `src/components/members/__tests__/` | 成员管理组件（创建/切换/声纹） |
| `src/components/voice/__tests__/` | 语音组件（面板/波形/消息气泡） |
| `src/services/__tests__/` | API 服务层（voiceApi, api） |
| `src/hooks/__tests__/` | React Hooks（语音模式/WebSocket/音频采集） |
| `src/stores/__tests__/` | Zustand Store（memberStore） |

**`tests/` 目录**（独立测试）：

| 位置 | 覆盖范围 |
|------|----------|
| `tests/components/auth/` | LoginForm, CaptchaImage |
| `tests/components/chat/` | MessageList, MessageInput 等 |
| `tests/hooks/` | useDocParse |
| `tests/services/` | mediaApi |
| `tests/settings/` | 模型配置/语音设置页面 |

### 3.3 运行测试

```bash
cd /home/dantsinghua/work/linchat/frontend

npm test                                        # 全部测试
npm test -- --watch                             # 监视模式
npm test -- tests/hooks/useDocParse.test.ts     # 指定文件
npm test -- --testPathPattern="voice"           # 按模式过滤
npm test -- --coverage                          # 覆盖率报告
```

### 3.4 编写前端测试

**组件测试**:

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

describe('ComponentName', () => {
  beforeEach(() => { jest.clearAllMocks(); });

  it('应正确渲染初始状态', () => {
    render(<ComponentName />);
    expect(screen.getByText('预期文本')).toBeInTheDocument();
  });
});
```

**Hook 测试**:

```tsx
import { renderHook, act } from '@testing-library/react';

jest.mock('@/services/mediaApi', () => ({
  createDocParseTask: jest.fn(),
}));

describe('useDocParse', () => {
  it('parse 成功后应更新状态', async () => {
    createDocParseTask.mockResolvedValue({ data: { task_id: 'task-123' } });
    const { result } = renderHook(() => useDocParse());
    await act(async () => { await result.current.parse('uuid'); });
    expect(result.current.status).toBe('pending');
  });
});
```

**Store 测试**:

```typescript
describe('memberStore', () => {
  beforeEach(() => { useMemberStore.setState(useMemberStore.getInitialState()); });

  it('应正确设置目标用户', () => {
    useMemberStore.getState().setTargetUser('user-123', '测试用户');
    expect(useMemberStore.getState().targetUserId).toBe('user-123');
  });
});
```

---

## 4. 端到端测试 (Playwright)

### 4.1 配置

**playwright.config.ts** 关键项：

- `testDir: './tests/e2e'`
- 浏览器: Chromium + Firefox
- `screenshot: 'only-on-failure'` — 失败时自动截图
- `trace: 'on-first-retry'` — 首次重试记录操作 trace
- `baseURL: 'http://localhost:3784'`
- CI 模式: 单 worker、2 次重试、禁止 `.only`
- Web 服务器: 自动构建并启动前端，本地复用已有服务器

### 4.2 测试规范

| 文件 | 场景 |
|------|------|
| `login-to-chat.spec.ts` | 登录流程、认证拦截、消息持久化、用户数据隔离 |
| `multimodal-image.spec.ts` | 图片上传、多模态消息展示 |
| `inference-cancel.spec.ts` | 推理取消（发送消息后点击停止） |
| `voice-interaction.spec.ts` | 语音模式交互（麦克风权限、录音、播放） |

### 4.3 运行 E2E 测试

**前置条件**: 完整后端服务栈运行中（PostgreSQL、Redis、Django、Celery）。

```bash
cd /home/dantsinghua/work/linchat/frontend

npx playwright install                          # 安装浏览器（首次）
npx playwright test                             # 全部 E2E
npx playwright test tests/e2e/login-to-chat.spec.ts  # 指定文件
npx playwright test --project=chromium          # 指定浏览器
npx playwright test --headed                    # 有头模式
npx playwright test --ui                        # UI 调试模式
npx playwright show-report                      # 查看报告
```

### 4.4 登录辅助模式

E2E 测试通过封装的 `login()` 函数处理认证：

```typescript
async function login(page: Page, username: string, password: string) {
  await page.goto(`${BASE_URL}/linchat/login`);
  await page.waitForSelector('img[alt="验证码"]', { timeout: 10000 });
  await page.fill('input[name="username"]', username);
  await page.fill('input[type="password"]', password);
  await page.fill('input[name="captcha"]', '1234');  // 测试环境固定验证码
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE_URL}/linchat/chat`, { timeout: 30000 });
}
```

> 注意：测试环境使用固定验证码 `1234` 或通过 Redis 容器查询实际验证码。

---

## 5. 测试模式与最佳实践

### 5.1 Mock 策略

**后端 Mock 对象**:

| Mock 目标 | 工具 | 说明 |
|-----------|------|------|
| Gateway HTTP | `patch` + `AsyncMock` | 文档解析、声纹注册等 |
| LLM API | `AsyncMock` | Agent 推理、流式输出 |
| Redis | `AsyncMock` | 会话状态、音频缓存、频率限制 |
| WebSocket | `patch("websockets.connect")` | ASR/TTS Gateway |
| MinIO | `MagicMock` | 文件上传/下载/删除 |
| Celery 任务 | `patch.object(task, "delay")` | 异步任务调度 |
| ffmpeg/ffprobe | `patch("subprocess.run")` | 音视频处理（无需实际安装） |

**前端 Mock 对象**:

| Mock 目标 | 工具 | 说明 |
|-----------|------|------|
| API 服务 | `jest.mock('@/services/...')` | HTTP 请求 |
| Next.js 路由 | 全局 `jest.setup.js` | `useRouter` 等 |
| 浏览器 API | `jest.fn()` | `matchMedia`, `AudioContext` |
| Zustand Store | `store.setState(initialState)` | 重置到初始状态 |

### 5.2 AsyncMock 使用模式

```python
@pytest.mark.asyncio
async def test_example():
    # AsyncMock 自动处理 await
    mock_redis = AsyncMock()
    mock_redis.get.return_value = b'cached_value'

    # 模拟异步生成器
    async def mock_stream(*args, **kwargs):
        yield StreamChunk(type="content", content="你好")
        yield StreamChunk(type="done", content="")

    with patch("apps.voice.services.redis_client", mock_redis):
        result = await service.process()
        mock_redis.get.assert_called_once_with("expected_key")
```

### 5.3 数据库测试隔离

- **TestCase**: 每个测试方法在事务中运行，结束后自动回滚。适用于大多数场景。
- **TransactionTestCase**: 真实事务提交，测试后清空数据表。用于 Celery 任务等需要提交的场景。
- **@pytest.mark.django_db**: 标记 pytest 函数式测试需要数据库访问。
- `--reuse-db` 复用已有测试数据库，避免每次运行重建。

### 5.4 SSE 流式测试

使用 `collect_stream` 辅助函数收集异步生成器输出：

```python
@pytest.mark.asyncio
async def test_sse_stream():
    async def mock_execute(*args, **kwargs):
        yield StreamChunk(type="content", content="你好")
        yield StreamChunk(type="done", content="", message_id=1)

    with patch("apps.graph.services.AgentService.execute", mock_execute):
        chunks = await collect_stream(service.stream_response(user_id=1))

    assert len(chunks) == 2
    assert chunks[0].content == "你好"
    assert chunks[1].type == "done"
```

### 5.5 语音管道测试

语音测试需要 Mock 多层异步组件（Consumer、Agent、TTS 管理器）：

```python
def _make_consumer() -> MagicMock:
    """创建 mock Consumer（实现 ConsumerProtocol）"""
    consumer = MagicMock()
    consumer._send_json = AsyncMock()
    consumer._send_binary = AsyncMock()
    return consumer

@pytest.mark.asyncio
async def test_voice_pipeline_normal():
    consumer = _make_consumer()
    with patch(f"{_VP}.AgentService") as mock_svc:
        mock_svc.return_value.execute = mock_agent_execute
        pipeline = VoicePipeline(consumer, user_id=1)
        await pipeline.run_pipeline("你好")
    consumer._send_json.assert_called()
```

### 5.6 测试命名规范

**后端**: 文件级文档字符串说明覆盖范围，方法名 `test_场景_描述`，每个方法配中文 docstring。

```python
class TestLLMExceptionMapping(TestCase):
    """LLM 异常映射测试"""

    def test_connection_error_mapping(self):
        """测试连接错误映射"""
```

**前端**: `describe` 分组功能区域，`it` 描述使用"应"字开头。

```typescript
describe('ComponentName', () => {
  describe('初始状态', () => {
    it('状态应为 idle', () => { ... });
  });
  describe('交互行为', () => {
    it('点击按钮后应提交表单', () => { ... });
  });
});
```

### 5.7 注意事项

1. 视频/音频测试 mock 了 ffmpeg/ffprobe 子进程，无需实际安装。
2. 异步测试中的 `asyncio.wait_for()` 配合 async generator 必须使用 `asyncio.shield()` 防取消（见 SSE 修复记录）。
3. 语音管道测试使用极短超时（0.05-0.1s）加速而非 mock 时间。
4. `test_media_cleanup_task.py` 等需要真实 PostgreSQL（`--reuse-db`）。
5. 前端 Store 测试前必须 `setState(getInitialState())` 重置状态。
6. 每个 `describe`/`beforeEach` 中调用 `jest.clearAllMocks()` 确保隔离。

---

## 6. 覆盖率报告

### 6.1 后端覆盖率

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 终端报告（含未覆盖行号）
pytest --cov=apps --cov-report=term-missing

# HTML 报告（输出到 htmlcov/）
pytest --cov=apps --cov-report=html

# 检查指定模块
pytest tests/chat/ --cov=apps.chat --cov-report=term-missing
pytest tests/voice/ --cov=apps.voice --cov-report=term-missing
```

### 6.2 前端覆盖率

```bash
cd /home/dantsinghua/work/linchat/frontend

npm test -- --coverage
```

覆盖率门槛配置在 `jest.config.js` 中: branches/functions/lines/statements 均为 80%。不达标时 Jest 返回非零退出码。

### 6.3 目标对照

| 层级 | 目标 | 检查命令 |
|------|------|----------|
| 后端总体 | >= 80% | `pytest --cov=apps` |
| 后端服务层 | >= 95% | `pytest --cov=apps.chat.services --cov=apps.voice.services ...` |
| 前端总体 | >= 80% | `npm test -- --coverage` |

---

## 7. 代码质量工具

### 7.1 后端

| 工具 | 用途 | 运行命令 | 检查模式 |
|------|------|----------|----------|
| Black | 代码格式化（88 字符行宽） | `black .` | `black --check .` |
| isort | 导入排序 | `isort .` | `isort --check-only .` |
| mypy | 类型检查 | `mypy .` | `mypy --strict apps/chat/` |

推荐执行顺序: `isort . && black . && mypy . && pytest`

### 7.2 前端

| 工具 | 用途 | 运行命令 | 配置 |
|------|------|----------|------|
| ESLint | 代码检查 | `npm run lint` | `next/core-web-vitals` 规则集 |
| Prettier | 代码格式化 | `npx prettier --write "src/**/*.{ts,tsx}"` | 单引号、100 字符行宽 |

推荐执行顺序: `npx prettier --write ... && npm run lint && npm test -- --coverage`

### 7.3 注意事项

- 项目**未配置 pre-commit hooks**，需开发者手动运行质量检查。
- 建议提交前至少运行格式化（black/isort 或 prettier）和代码检查（mypy 或 lint）。

---

## 8. 常用命令速查

### 后端

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 测试
pytest                                          # 全部
pytest tests/chat/ -v                           # 聊天模块
pytest tests/voice/ -v                          # 语音模块
pytest tests/apps/graph/ -v                     # Agent 模块
pytest tests/users/ -v                          # 用户模块
pytest tests/memory/ -v                         # 记忆模块
pytest -m asyncio -v                            # 仅异步测试
pytest -k "test_connection" -v                  # 关键字过滤

# 覆盖率
pytest --cov=apps --cov-report=term-missing     # 终端报告
pytest --cov=apps --cov-report=html             # HTML 报告

# 质量
isort . && black . && mypy .
```

### 前端

```bash
cd /home/dantsinghua/work/linchat/frontend

# 单元测试
npm test                                        # 全部
npm test -- --watch                             # 监视模式
npm test -- --coverage                          # 覆盖率
npm test -- --testPathPattern="voice"           # 过滤

# E2E
npx playwright install                          # 安装浏览器
npx playwright test                             # 全部 E2E
npx playwright test --headed                    # 有头模式
npx playwright test --ui                        # UI 调试
npx playwright show-report                      # 查看报告

# 质量
npm run lint
npx prettier --write "src/**/*.{ts,tsx}"
```

### 完整提交前检查

```bash
# 后端
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
isort . && black . && mypy . && pytest --cov=apps --cov-report=term-missing

# 前端
cd /home/dantsinghua/work/linchat/frontend
npx prettier --write "src/**/*.{ts,tsx,js,jsx}" && npm run lint && npm test -- --coverage
```

---

## 相关文档

- [编码规范](code-standards.md) -- 代码风格、命名约定、架构约束
- [代码库概览](codebase-summary.md) -- 项目结构、技术栈、模块职责
- [宪法文件](../.specify/memory/constitution.md) -- 不可违背的原则和约束
- [代码示例](constitution-examples.md) -- 编码时强制参考的示例代码
