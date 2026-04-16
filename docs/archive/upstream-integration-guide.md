# LLM Gateway 上游对接指南

> **版本**: 2.0.0
> **更新日期**: 2026-02-11
> **适用版本**: LLM Gateway v2.x（含 NeMo Guardrails 混合护栏）
> **协议兼容**: OpenAI Chat Completions API

本文档面向上游应用开发者，详细说明 LLM Gateway 的所有 API 端点、请求/响应契约、错误码体系、安全护栏行为和对接注意事项。

---

## 目录

1. [接入概述](#1-接入概述)
2. [认证与鉴权](#2-认证与鉴权)
3. [可用模型一览](#3-可用模型一览)
4. [API 端点详细说明](#4-api-端点详细说明)
   - 4.1 [聊天推理](#41-聊天推理-post-v1chatcompletions)
   - 4.2 [文本向量化](#42-文本向量化-post-v1embeddings)
   - 4.3 [文档解析](#43-文档解析)
   - 4.4 [模型管理](#44-模型管理)
   - 4.5 [健康检查](#45-健康检查)
   - 4.6 [Prometheus 指标](#46-prometheus-指标)
5. [安全护栏体系](#5-安全护栏体系)
6. [错误码完整参考](#6-错误码完整参考)
7. [流式响应协议](#7-流式响应协议)
8. [对接最佳实践](#8-对接最佳实践)

---

## 1. 接入概述

### 1.1 网络架构

LLM Gateway 通过 STCP 隧道对外提供服务，上游应用需部署 frpc visitor 后，通过本地端口访问网关。

```
上游应用 → http://127.0.0.1:{visitor_port}/v1/...
              ↓ (frpc visitor → frps 中转 → frpc proxy)
          WSL2 Gateway (172.19.181.143:8081)
```

上游应用无需关心隧道细节，只需将 Base URL 配置为 visitor 绑定的本地地址即可。

### 1.2 Base URL

```
http://127.0.0.1:{visitor_port}
```

其中 `{visitor_port}` 为上游 frpc visitor 配置的 `bindPort`（如 8100）。

### 1.3 兼容性

网关完全兼容 **OpenAI Chat Completions API** 格式，可直接使用 OpenAI SDK 对接：

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-23h8ugn3828910h8g308979y4",
    base_url="http://127.0.0.1:8100/v1",
)

response = client.chat.completions.create(
    model="qwen3-8b",
    messages=[{"role": "user", "content": "你好"}],
)
```

---

## 2. 认证与鉴权

### 2.1 认证方式

所有 API 请求（健康检查和 Prometheus 端点除外）必须携带 API Key：

```
Authorization: Bearer <API_KEY>
```

### 2.2 免认证路径

以下路径不需要 API Key：

| 路径 | 说明 |
|------|------|
| `/` | 根路径 |
| `/health` | 完整健康检查 |
| `/health/live` | 存活探针 |
| `/health/ready` | 就绪探针 |
| `/health/gpu` | GPU 状态 |
| `/metrics` | Prometheus 指标 |
| `/docs` | Swagger UI |
| `/openapi.json` | OpenAPI Schema |

### 2.3 认证失败响应

```json
{
  "error": {
    "code": "E2001",
    "message": "缺少认证信息",
    "type": "authentication_error",
    "request_id": "req-xxxxxxxx"
  }
}
```

HTTP 状态码: `401 Unauthorized`

API Key 无效时：

```json
{
  "error": {
    "code": "E2003",
    "message": "API Key 无效",
    "type": "authentication_error",
    "request_id": "req-xxxxxxxx"
  }
}
```

### 2.4 请求追踪

每个请求会自动分配或继承 `X-Request-ID`：

- 客户端可在请求头中传入 `X-Request-ID`，网关会沿用
- 未传入时，网关自动生成 UUID
- 响应头中始终包含 `X-Request-ID`，可用于问题排查

---

## 3. 可用模型一览

### 3.1 本地推理模型

| 模型 ID | 用途 | 上下文长度 | 最大输入 | 最大输出 | 显存需求 | 特殊能力 |
|---------|------|-----------|---------|---------|---------|---------|
| `qwen3-8b` | 通用对话 | 8K | 7K | 4K | 16GB | 热切换、Tool Calling、思维链 |
| `qwen3-30b` | 高质量长文本 | 32K | 30K | 8K | 18GB | 长上下文 |
| `qwen2.5-coder` | 代码生成 | 32K | 30K | 8K | 15GB | 热切换、代码补全 |
| `qwen2.5-vl` | 文档 OCR（单图） | 32K | 30K | 8K | 15GB | 热切换、视觉理解、单图推理 |
| `minicpm-v` | 文档 OCR（多图） | 4K | 3.5K | 3K | 18GB | 热切换、视觉理解、多图推理、视频 |
| `minicpm-o` | 全能多模态 | 4K | 3.5K | 3K | 21.6GB | 热切换、视觉+音频+视频 |
| `qwen3-embedding` | 文本向量化 | 8K | 8K | - | 4GB | 语义搜索、聚类 |

> **注意**: 由于 GPU 显存限制，同一时间只能运行一个本地模型。请求未加载的模型时，网关会自动进行热切换（卸载当前模型 → 加载目标模型），切换期间请求会等待。

### 3.2 远程转发模型

| 模型 ID | 提供商 | 上下文长度 | 说明 |
|---------|--------|-----------|------|
| `gpt-4o` | OpenAI | 128K | 需配置 OPENAI_API_KEY |
| `deepseek-chat` | DeepSeek | 64K | 需配置 DEEPSEEK_API_KEY |

远程模型不占用 GPU 显存，可随时使用。

### 3.3 VL 模型（文档解析专用）

文档解析 API 仅接受以下 VL 模型：

| 模型 ID | 推理模式 | 多图阈值 | 适用场景 |
|---------|---------|---------|---------|
| `qwen2.5-vl` | 逐页推理 | - | 通用文档 OCR |
| `minicpm-v` | 多图推理优化 | ≤5 页 | 短文档快速解析 |
| `minicpm-o` | 多图推理优化 | ≤5 页 | 多模态文档（含音频/视频） |

> `minicpm-v` 和 `minicpm-o` 共用端口 8006，同一时间只能使用其中一个。

---

## 4. API 端点详细说明

### 4.1 聊天推理 `POST /v1/chat/completions`

核心端点，完全兼容 OpenAI Chat Completions API。

#### 请求参数

| 参数 | 类型 | 必填 | 默认值 | 约束 | 说明 |
|------|------|------|--------|------|------|
| `model` | string | **是** | - | 1-100字符, `^[a-zA-Z0-9._-]+$` | 模型 ID |
| `messages` | array | **是** | - | 1-100条 | 消息列表 |
| `temperature` | float | 否 | 0.7 | 0.0-2.0 | 温度参数，越高越随机 |
| `max_tokens` | int | 否 | 模型默认 | 1-128000 | 最大生成 token 数 |
| `top_p` | float | 否 | null | 0.0-1.0 | 核采样参数 |
| `stream` | bool | 否 | false | - | 是否流式输出 |
| `enable_thinking` | bool | 否 | null | - | Qwen3 思维链控制 |
| `tools` | array | 否 | null | - | 工具定义列表（Tool Calling） |
| `tool_choice` | string/object | 否 | null | `auto`/`none`/`required`/指定函数 | 工具选择策略 |
| `guardrails_enabled` | bool | 否 | true | - | 全局护栏开关（见 [§5](#5-安全护栏体系)） |
| `guardrails_level` | string | 否 | `"fast"` | `fast`/`standard`/`deep` | 护栏级别（见 [§5](#5-安全护栏体系)） |
| `include_usage` | bool | 否 | false | - | 是否返回护栏元数据 |

#### 消息格式 (messages)

每条消息包含以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `role` | string | 是 | `system` / `user` / `assistant` / `tool` |
| `content` | string 或 array | 是* | 消息内容（tool 和 assistant+tool_calls 时可为 null） |
| `tool_call_id` | string | 仅 tool | 关联的工具调用 ID |
| `tool_calls` | array | 仅 assistant | 工具调用结果列表 |

**多模态内容**（content 为 array 时）：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "描述这张图片"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]
}
```

支持的内容类型：`text`、`image_url`、`audio_url`、`video_url`。

#### 非流式响应

HTTP 状态码: `200 OK`

```json
{
  "id": "chatcmpl-abc123def456",
  "object": "chat.completion",
  "created": 1707825600,
  "model": "qwen3-8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！有什么可以帮你的？"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 15,
    "total_tokens": 25
  }
}
```

**finish_reason 取值**:

| 值 | 含义 |
|------|------|
| `stop` | 自然结束（遇到停止符或内容完整生成） |
| `length` | 达到 max_tokens 限制 |
| `content_filter` | 安全护栏拦截了输出内容 |
| `tool_calls` | 模型请求调用工具 |

#### 含护栏元数据的响应（`include_usage=true`）

当启用深度护栏且设置 `include_usage=true` 时，响应体包含额外的顶层字段：

```json
{
  "id": "chatcmpl-abc123def456",
  "object": "chat.completion",
  "created": 1707825600,
  "model": "qwen3-8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "比特币的创始人是中本聪（Satoshi Nakamoto）..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  },
  "warnings": [
    {
      "type": "fact_check_failed",
      "confidence": 0.92,
      "details": "部分陈述与网络搜索结果不一致"
    }
  ],
  "guardrails_metadata": {
    "activated_rails": ["input:jailbreak", "input:yara", "output:hallucination", "output:facts"],
    "timings": {
      "input:jailbreak": 0.048,
      "input:yara": 0.012,
      "output:hallucination": 2.35,
      "output:facts": 4.18
    },
    "warnings_triggered": true,
    "total_duration": 6.59
  }
}
```

**字段说明**:

| 字段 | 类型 | 条件 | 说明 |
|------|------|------|------|
| `warnings` | array | 护栏触发软拦截时出现 | 警告条目数组，每条包含 `type`、`confidence`、`details` |
| `guardrails_metadata` | object | `include_usage=true` 时出现 | 护栏执行详情 |
| `guardrails_metadata.activated_rails` | array | - | 已激活的护栏列表，格式 `"阶段:类型"` |
| `guardrails_metadata.timings` | object | - | 各护栏执行延迟（秒） |
| `guardrails_metadata.warnings_triggered` | bool | - | 是否有护栏触发了软拦截 |
| `guardrails_metadata.total_duration` | float | - | 护栏链总延迟（秒） |

**warnings 条目的 type 取值**:

| type | 含义 |
|------|------|
| `hallucination_detected` | 检测到 LLM 输出包含自相矛盾的陈述 |
| `fact_check_failed` | LLM 输出的事实陈述与网络搜索结果不一致 |

> **重要**: `warnings` 是**软拦截**——完整的 LLM 响应仍会返回，`warnings` 仅作为风险提示。上游应用可根据 `confidence` 值自行决定是否展示给终端用户。置信度阈值为 **0.7**，高于此值触发警告。

#### Tool Calling 示例

**请求**:

```json
{
  "model": "qwen3-8b",
  "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "查询指定城市的天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "城市名称"}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

**模型返回工具调用**:

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"city\": \"北京\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

**提交工具执行结果**:

```json
{
  "model": "qwen3-8b",
  "messages": [
    {"role": "user", "content": "北京今天天气怎么样？"},
    {"role": "assistant", "content": null, "tool_calls": [{"id": "call_abc123", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\": \"北京\"}"}}]},
    {"role": "tool", "tool_call_id": "call_abc123", "content": "{\"temperature\": 15, \"condition\": \"晴\"}"}
  ]
}
```

#### curl 示例

```bash
# 基本对话
curl -X POST http://127.0.0.1:8100/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "你好"}],
    "temperature": 0.7,
    "max_tokens": 512
  }'

# 启用深度护栏 + 元数据
curl -X POST http://127.0.0.1:8100/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "比特币的创始人是谁？"}],
    "guardrails_level": "deep",
    "include_usage": true
  }'

# 流式输出
curl -X POST http://127.0.0.1:8100/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "讲一个短故事"}],
    "stream": true
  }' --no-buffer

# 关闭所有护栏（高危模式，仅限测试/开发环境）
curl -X POST http://127.0.0.1:8100/v1/chat/completions \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-8b",
    "messages": [{"role": "user", "content": "测试内容"}],
    "guardrails_enabled": false
  }'
```

---

### 4.2 文本向量化 `POST /v1/embeddings`

将输入文本转换为向量表示（Embedding），用于语义搜索、文本聚类等场景。

#### 请求参数

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `model` | string | 是 | 1-100字符, `^[a-zA-Z0-9._-]+$` | Embedding 模型 ID（如 `qwen3-embedding`） |
| `input` | string 或 array | 是 | 非空 | 输入文本（单条字符串或字符串数组） |
| `encoding_format` | string | 否 | - | 编码格式（如 `float`） |

#### 响应

HTTP 状态码: `200 OK`

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.0123, -0.0456, 0.0789, ...],
      "index": 0
    }
  ],
  "model": "qwen3-embedding",
  "usage": {
    "prompt_tokens": 5,
    "total_tokens": 5
  }
}
```

#### curl 示例

```bash
# 单条文本
curl -X POST http://127.0.0.1:8100/v1/embeddings \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-embedding",
    "input": "用于语义搜索的文本"
  }'

# 批量文本
curl -X POST http://127.0.0.1:8100/v1/embeddings \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-embedding",
    "input": ["文本一", "文本二", "文本三"]
  }'
```

---

### 4.3 文档解析

将 PDF/DOCX 文档转换为 Markdown 文本，采用异步任务模式。

#### 4.3.1 创建解析任务 `POST /v1/documents/parse`

上传文件并创建异步解析任务。

**请求格式**: `multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | **是** | 上传的文件（PDF 或 DOCX） |
| `model` | string | **是** | VL 模型 ID（`qwen2.5-vl` / `minicpm-v` / `minicpm-o`） |
| `pages` | string | 否 | 指定解析页码范围，如 `"1,3-5,8"`。不传则解析全部页面 |

**文件限制**:

| 限制项 | 值 | 超限错误码 |
|--------|-----|-----------|
| 文件大小 | ≤ 10MB | E6001 (413) |
| 页数上限 | ≤ 200 页 | E6006 (400) |
| 支持格式 | PDF, DOCX | E6002 (400) |
| 加密文件 | 不支持 | E6004 (400) |

**成功响应** (HTTP `202 Accepted`):

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "model": "qwen2.5-vl",
  "file_name": "report.pdf",
  "file_type": "pdf",
  "total_pages": 15,
  "requested_pages": "1,3-5",
  "progress": {
    "current": 0,
    "total": 4
  },
  "error_message": null,
  "created_at": "2026-02-11T10:30:00Z",
  "completed_at": null
}
```

**model 参数错误（非 VL 模型）**:

HTTP 状态码: `400 Bad Request`

```json
{
  "error": {
    "code": "E3001",
    "message": "模型 qwen3-8b 不存在或不是 VL 模型",
    "type": "invalid_request_error",
    "request_id": "req-xxx",
    "details": {
      "available_vl_models": ["qwen2.5-vl", "minicpm-v", "minicpm-o"]
    }
  }
}
```

#### curl 示例

```bash
# 解析 PDF（全部页面）
curl -X POST http://127.0.0.1:8100/v1/documents/parse \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@report.pdf" \
  -F "model=qwen2.5-vl"

# 解析指定页码
curl -X POST http://127.0.0.1:8100/v1/documents/parse \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@document.pdf" \
  -F "model=minicpm-v" \
  -F "pages=1,3-5,8"
```

#### 4.3.2 查询任务状态 `GET /v1/documents/tasks/{task_id}`

轮询任务进度。

**成功响应** (HTTP `200 OK`):

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "model": "qwen2.5-vl",
  "file_name": "report.pdf",
  "file_type": "pdf",
  "total_pages": 15,
  "requested_pages": null,
  "progress": {
    "current": 7,
    "total": 15
  },
  "error_message": null,
  "created_at": "2026-02-11T10:30:00Z",
  "completed_at": null
}
```

**任务状态 (status) 说明**:

| 状态 | 含义 | 后续操作 |
|------|------|---------|
| `pending` | 排队等待中 | 继续轮询 |
| `processing` | 正在解析 | 继续轮询，观察 `progress.current` 增长 |
| `completed` | 解析完成 | 调用结果获取接口 |
| `failed` | 解析失败 | 检查 `error_message` 字段 |

**任务不存在** (HTTP `404`):

```json
{
  "error": {
    "code": "E3001",
    "message": "任务 xxx 不存在",
    "type": "not_found",
    "request_id": "req-xxx"
  }
}
```

**任务已过期** (HTTP `410 Gone`)——任务创建超过 7 天：

```json
{
  "error": {
    "code": "E6009",
    "message": "任务已过期（超过 7 天）",
    "type": "invalid_request_error",
    "request_id": "req-xxx",
    "details": {
      "task_id": "xxx"
    }
  }
}
```

**失败任务响应**（包含建议操作）:

```json
{
  "task_id": "xxx",
  "status": "failed",
  "error_message": "GPU 显存不足，解析任务延迟执行",
  "suggestion": "请检查文件格式或稍后重试",
  ...
}
```

#### 4.3.3 获取解析结果 `GET /v1/documents/tasks/{task_id}/result`

| 查询参数 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `format` | string | `"markdown"` | 响应格式：`markdown` 或 `json` |

**Markdown 格式响应** (`format=markdown`):

HTTP 状态码: `200 OK`
Content-Type: `text/markdown`

```markdown
# 文档标题

第一页的内容...

---

## 第二章

第二页的内容...
```

**JSON 格式响应** (`format=json`):

HTTP 状态码: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "model": "qwen2.5-vl",
  "total_pages": 15,
  "pages": [
    {
      "page_number": 1,
      "markdown": "# 文档标题\n\n第一页内容...",
      "confidence": 0.95,
      "latency_ms": 1200.5
    },
    {
      "page_number": 2,
      "markdown": "## 第二章\n\n第二页内容...",
      "confidence": 0.92,
      "latency_ms": 980.3
    }
  ],
  "usage": {
    "total_prompt_tokens": 5000,
    "total_completion_tokens": 3200,
    "total_tokens": 8200
  },
  "total_latency_ms": 15000.8
}
```

**任务未完成** (HTTP `409 Conflict`):

```json
{
  "error": {
    "code": "TASK_NOT_COMPLETED",
    "message": "任务尚未完成",
    "type": "conflict",
    "request_id": "req-xxx",
    "details": {
      "status": "processing",
      "progress": {
        "current": 7,
        "total": 15
      }
    }
  }
}
```

**任务失败** (HTTP `200 OK`):

```json
{
  "task_id": "xxx",
  "status": "failed",
  "error_message": "VL 模型推理超时"
}
```

#### 4.3.4 文档解析完整流程

```
1. POST /v1/documents/parse  →  获取 task_id (HTTP 202)
2. 轮询 GET /v1/documents/tasks/{task_id}
   ├─ status=pending     →  继续轮询（建议间隔 2-3 秒）
   ├─ status=processing  →  继续轮询，观察 progress
   ├─ status=completed   →  进入步骤 3
   └─ status=failed      →  读取 error_message，结束
3. GET /v1/documents/tasks/{task_id}/result  →  获取 Markdown/JSON 结果
```

**建议轮询策略**: 初始间隔 2 秒，每次递增 1 秒，最大间隔 10 秒。任务客户端断开后，后端继续执行，结果可稍后查询。

---

### 4.4 模型管理

#### 4.4.1 获取模型列表 `GET /v1/models`

**响应** (HTTP `200 OK`):

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3-8b",
      "object": "model",
      "created": 0,
      "owned_by": "local",
      "status": "running",
      "vram_usage_gb": 16.0,
      "port": 8001
    },
    {
      "id": "gpt-4o",
      "object": "model",
      "created": 0,
      "owned_by": "local",
      "status": "running",
      "vram_usage_gb": null,
      "port": null
    }
  ]
}
```

**status 取值**:

| 状态 | 含义 |
|------|------|
| `unloaded` | 未加载，需要先加载才能使用 |
| `loading` | 正在加载中 |
| `running` | 运行中，可接受请求 |
| `sleeping` | 休眠中（已释放 GPU 显存，可快速唤醒） |
| `error` | 加载失败或运行异常 |

#### 4.4.2 获取模型详情 `GET /v1/models/{model_id}`

返回模型的完整信息，包含配置参数和运行状态。

**响应** (HTTP `200 OK`):

```json
{
  "id": "qwen3-8b",
  "object": "model",
  "created": 0,
  "owned_by": "local",
  "status": "running",
  "vram_usage_gb": 16.0,
  "port": 8001,
  "display_name": "Qwen3-8B",
  "inference_engine": "vllm",
  "max_context_length": 8192,
  "max_input_tokens": 7168,
  "max_output_tokens": 4096,
  "supports_hot_swap": true,
  "supports_streaming": true,
  "priority": 1,
  "process_pid": 12345,
  "last_used_at": "2026-02-11T10:30:00Z",
  "loaded_at": "2026-02-11T08:00:00Z",
  "error_message": null
}
```

#### 4.4.3 加载模型 `POST /v1/models/load`

**请求体**:

```json
{
  "model": "qwen3-8b",
  "priority": false
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 模型 ID |
| `priority` | bool | 否 | 优先级加载（抢占当前模型） |

**响应** (HTTP `200 OK`):

```json
{
  "model": "qwen3-8b",
  "status": "running",
  "message": "模型 'qwen3-8b' 已加载并运行中",
  "estimated_time_seconds": null
}
```

#### 4.4.4 卸载模型 `POST /v1/models/unload`

**请求体**:

```json
{
  "model": "qwen3-8b",
  "force": false
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 模型 ID |
| `force` | bool | 否 | 强制卸载（即使有活跃请求） |

**响应** (HTTP `200 OK`):

```json
{
  "model": "qwen3-8b",
  "status": "unloaded",
  "vram_freed_gb": 16.0
}
```

#### 4.4.5 模型休眠 `POST /v1/models/{model_id}/sleep`

将模型从 GPU 显存转移到 CPU RAM 或磁盘，释放 GPU 资源但保持快速唤醒能力。

**请求体**（可选）:

```json
{
  "level": 1
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `level` | int | 1 | 1=CPU RAM（快速唤醒），2=磁盘（节省更多内存） |

**响应** (HTTP `200 OK`):

```json
{
  "model": "qwen3-8b",
  "status": "sleeping",
  "sleep_level": 1,
  "vram_freed_gb": 16.0
}
```

#### 4.4.6 唤醒模型 `POST /v1/models/{model_id}/wake`

从休眠状态恢复模型到 GPU 显存。

**响应** (HTTP `200 OK`):

```json
{
  "model": "qwen3-8b",
  "status": "running",
  "wake_time_ms": 850.5
}
```

#### 4.4.7 获取性能指标 `GET /v1/models/{model_id}/metrics`

| 查询参数 | 类型 | 默认值 | 允许值 | 说明 |
|---------|------|--------|--------|------|
| `window` | int | 300 | 60, 300, 3600 | 统计窗口（秒） |

**响应** (HTTP `200 OK`):

```json
{
  "model": "qwen3-8b",
  "window_seconds": 300,
  "sample_count": 42,
  "ttft": {
    "avg_ms": 245.6,
    "p50_ms": 210.3,
    "p95_ms": 580.2,
    "p99_ms": 1200.0
  },
  "tps": {
    "avg": 35.2,
    "p50": 38.5,
    "p95": 52.1
  }
}
```

| 指标 | 含义 |
|------|------|
| `ttft` | Time To First Token — 首 token 延迟 |
| `tps` | Tokens Per Second — 生成速度 |

---

### 4.5 健康检查

#### 4.5.1 完整健康检查 `GET /health`

检查所有组件（网关、数据库、Redis、模型服务、护栏）。

HTTP `200` 或 `503`（有组件不健康时）。

#### 4.5.2 存活探针 `GET /health/live`

仅检查网关进程是否存活。用于 K8s liveness probe。

HTTP `200`（始终返回，除非进程崩溃）。

#### 4.5.3 就绪探针 `GET /health/ready`

检查关键依赖是否就绪。用于 K8s readiness probe。

HTTP `200` 或 `503`（依赖未就绪）。

#### 4.5.4 GPU 状态 `GET /health/gpu`

**响应** (HTTP `200 OK`):

```json
{
  "status": "available",
  "device_id": 0,
  "device_name": "NVIDIA GeForce RTX 4090",
  "total_gb": 24.0,
  "used_gb": 16.5,
  "free_gb": 7.5,
  "effective_free_gb": 5.5,
  "utilization_pct": 68.75,
  "reserved_gb": 2.0,
  "is_warning": false,
  "is_critical": false,
  "timestamp": "2026-02-11T10:30:00Z",
  "error": null,
  "models": []
}
```

---

### 4.6 Prometheus 指标

`GET /metrics`

返回 Prometheus 格式文本，可直接被 Prometheus server 抓取。不需要认证。

---

## 5. 安全护栏体系

网关内置多层安全护栏，保护 LLM 免受攻击并确保输出质量。

### 5.1 护栏架构总览

```
请求进入
  │
  ├── guardrails_enabled=false → 完全跳过，直接推理 ──→ 返回响应
  │
  └── guardrails_enabled=true
        │
        ├── [自研规则引擎] PII / Prompt 注入 / 有害内容检测 (<10ms)
        │     └── 检测到 → 硬拦截 403（E4001/E4002/E4003）
        │
        ├── guardrails_level=fast → 到此为止 ──→ LLM 推理 → 返回响应
        │
        ├── [NeMo 阶段 1] Jailbreak Detection + YARA 规则 (~60ms)
        │     └── 检测到 → 硬拦截 403（E4004/E4005）
        │
        ├── guardrails_level=standard → 到此为止 ──→ LLM 推理 → 返回响应
        │
        └── guardrails_level=deep → LLM 推理 →
              │
              ├── [NeMo 阶段 2] Self-Check Hallucination
              │     └── 检测到 → 软拦截（返回响应 + warnings）
              │
              └── [NeMo 阶段 2] Self-Check Facts（Tavily 网络搜索）
                    └── 检测到 → 软拦截（返回响应 + warnings）
```

### 5.2 护栏级别 (guardrails_level)

| 级别 | 包含的护栏 | 延迟影响 | 适用场景 |
|------|-----------|---------|---------|
| `fast`（默认） | 仅自研规则引擎 | < 10ms | 低延迟场景、通用对话 |
| `standard` | 自研引擎 + NeMo 阶段 1（Jailbreak + YARA） | < 70ms (P95) | 需要防护注入攻击的场景 |
| `deep` | 自研引擎 + NeMo 全部（阶段 1 + 阶段 2） | < 30s (P95) | 金融咨询、医疗建议等高风险场景 |

### 5.3 硬拦截（Hard Block）

硬拦截在检测到明确的安全威胁时立即返回 **403 Forbidden**，**不执行 LLM 推理**。

**响应格式**:

```json
{
  "error": {
    "code": "E4001",
    "message": "检测到潜在的安全风险，请求已被拦截",
    "type": "safety_error",
    "request_id": "req-xxx",
    "details": {
      "rule": "prompt_injection_pattern",
      "confidence": 1.0
    }
  }
}
```

**硬拦截错误码**:

| 错误码 | HTTP | 含义 | 触发条件 |
|--------|------|------|---------|
| E4001 | 403 | Prompt 注入检测 | 自研引擎检测到 Prompt 注入模式 |
| E4002 | 403 | PII 检测 | 自研引擎检测到个人身份信息（拦截模式） |
| E4003 | 403 | 有害内容拦截 | 自研引擎检测到有害/违规内容 |
| E4004 | 403 | NSFW 图像拦截 | 多模态输入中检测到不当图像 |

**NeMo 阶段 1 硬拦截错误码**（`guardrails_level=standard` 或 `deep`）:

NeMo 阶段 1 的硬拦截复用 GatewayException 标准格式，`details` 字段统一包含 `rule`（规则名称）和 `confidence`（置信度，硬拦截时默认 1.0）：

| 错误码 | HTTP | 含义 | 触发条件 |
|--------|------|------|---------|
| E4004 | 403 | 越狱攻击检测 | NeMo Jailbreak Detection（困惑度启发式） |
| E4005 | 403 | 代码注入检测 | NeMo YARA 规则（SQL/XSS/命令注入） |

**Jailbreak 硬拦截响应示例**:

```json
{
  "error": {
    "code": "E4004",
    "message": "Jailbreak attack detected",
    "type": "safety_error",
    "request_id": "req-xxx",
    "details": {
      "rule": "jailbreak_heuristic",
      "confidence": 1.0
    }
  }
}
```

**YARA 代码注入硬拦截响应示例**:

```json
{
  "error": {
    "code": "E4005",
    "message": "Code injection detected",
    "type": "safety_error",
    "request_id": "req-xxx",
    "details": {
      "rule": "sql_injection,xss_pattern",
      "confidence": 1.0
    }
  }
}
```

> **注意**: YARA 匹配多个规则时，`rule` 字段用逗号连接规则名称。

### 5.4 软拦截（Soft Block）

软拦截在检测到内容质量问题时，**返回完整的 LLM 响应**，同时附加 `warnings` 数组字段作为风险提示。

仅在 `guardrails_level=deep` 时可能触发。

**响应体中的 warnings 字段**:

```json
{
  "warnings": [
    {
      "type": "hallucination_detected",
      "confidence": 0.85,
      "details": "检测到模型输出包含自相矛盾的陈述"
    },
    {
      "type": "fact_check_failed",
      "confidence": 0.92,
      "details": "部分事实陈述与网络搜索结果不一致"
    }
  ]
}
```

**上游处理建议**:

- `confidence >= 0.9` → 强烈建议向用户展示警告
- `0.7 <= confidence < 0.9` → 可选择性展示
- 多条 warnings 表示多个护栏同时触发

### 5.5 全局护栏开关 (guardrails_enabled)

当 `guardrails_enabled=false` 时：

- 完全跳过所有安全检查（自研引擎 + NeMo 护栏）
- `guardrails_level` 参数被忽略
- 不返回 `warnings` 和 `guardrails_metadata`
- 审计日志记录 WARNING 级别事件（`guardrails_bypassed: true`）

> **警告**: 此为高危模式，仅建议在开发/测试环境或受信任场景使用。所有使用记录均会被审计。

### 5.6 短路优化

护栏链采用短路策略执行：

1. 自研规则引擎检测到恶意内容 → **立即返回 403**，跳过 NeMo 和 LLM 推理
2. NeMo 阶段 1 检测到威胁 → **立即返回 403**，跳过 LLM 推理
3. 任何环节拦截后，后续所有检查被跳过，避免冗余计算

### 5.7 降级与 Fail-Open 机制

当护栏服务出现异常时，系统按以下链路自动降级：

```
NeMo 护栏超时/失败
  → 降级到自研规则引擎
    → 自研引擎也失败
      → Fail-Open 放行（不阻塞请求，审计日志记录）
```

**对上游的影响**:

- 降级过程对上游**完全透明**，请求不会被阻塞
- 降级时响应中不会包含 `guardrails_metadata`（因为护栏未成功执行）
- Fail-Open 放行的请求在服务端审计日志中标记 `fail_open=true`
- 核心聊天功能可用性保持 ≥ 99.9%

---

## 6. 错误码完整参考

### 6.1 统一错误响应格式

所有错误响应遵循统一格式：

```json
{
  "error": {
    "code": "Exxxx",
    "message": "人类可读的错误描述",
    "type": "error_type",
    "request_id": "req-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "details": {}
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 错误码（格式 Exxxx） |
| `message` | string | 错误描述（可直接展示给用户） |
| `type` | string | OpenAI 兼容的错误类型 |
| `request_id` | string | 请求 ID，用于问题排查 |
| `details` | object | 额外的上下文信息（可选） |

### 6.2 通用错误 (1xxx)

| 错误码 | HTTP | type | 默认消息 | 触发场景 |
|--------|------|------|---------|---------|
| E1000 | 500 | `server_error` | 服务器内部错误 | 未预期的内部异常 |
| E1001 | 400 | `validation_error` | 请求验证失败 | 参数格式错误、空消息、字段不合法 |
| E1002 | 429 | `rate_limit_error` | 请求频率超限，请稍后重试 | 超过速率限制 |

**E1001 常见触发场景**:

- `model` 字段为空或包含非法字符
- `messages` 数组为空
- `temperature` 超出 0.0-2.0 范围
- `max_tokens` 超出 1-128000 范围
- 消息 `content` 为空字符串

### 6.3 认证授权错误 (2xxx)

| 错误码 | HTTP | type | 默认消息 | 触发场景 |
|--------|------|------|---------|---------|
| E2001 | 401 | `authentication_error` | 缺少认证信息 | 请求未携带 Authorization 头 |
| E2002 | 403 | `permission_error` | 无访问权限 | 权限不足 |
| E2003 | 401 | `authentication_error` | API Key 无效 | API Key 格式正确但无法验证 |
| E2004 | 429 | `rate_limit_error` | 配额已用完，请联系管理员 | API Key 关联的配额耗尽 |

### 6.4 模型服务错误 (3xxx)

| 错误码 | HTTP | type | 默认消息 | 触发场景 | details 示例 |
|--------|------|------|---------|---------|-------------|
| E3001 | 404 | `invalid_request_error` | 请求的模型不存在 | `model` 参数指定了不存在的模型 | `{"model_id": "xxx"}` |
| E3002 | 503 | `unavailable_error` | 模型服务暂时不可用 | 模型正在加载/切换中、引擎进程崩溃 | `{"retry_after": 10}` |
| E3003 | 504 | `timeout_error` | 模型服务响应超时 | 推理超时 | `{"timeout_seconds": 30}` |
| E3004 | 400 | `invalid_request_error` | 请求内容超过模型上下文长度限制 | 消息总 token 超过模型 max_context_length | `{"estimated_tokens": 12000, "max_tokens": 8192}` |

**E3002 重试建议**: 如果 `details` 中包含 `retry_after` 字段，表示建议的重试等待时间（秒）。

### 6.5 安全护栏错误 (4xxx)

| 错误码 | HTTP | type | 默认消息 | 触发层 | details 字段 |
|--------|------|------|---------|--------|-------------|
| E4001 | 403 | `safety_error` | 检测到潜在的安全风险，请求已被拦截 | 自研引擎 | `{"triggered_rules": [...], "confidence": 0.95}` |
| E4002 | 403 | `safety_error` | 检测到敏感个人信息，请求已被拦截 | 自研引擎 | `{"pii_types": ["phone", "id_card"]}` |
| E4003 | 403 | `safety_error` | 抱歉，我无法提供该内容 | 自研引擎 | `{"categories": ["violence", "hate"]}` |
| E4004 | 403 | `safety_error` | 越狱攻击/NSFW 图像被拦截 | NeMo 阶段 1 / 多模态检测 | `{"rule": "jailbreak_heuristic", "confidence": 1.0}` |
| E4005 | 403 | `safety_error` | 代码注入检测 | NeMo YARA 引擎 | `{"rule": "sql_injection,xss_pattern", "confidence": 1.0}` |

> **注意**: E4004 同时用于 NSFW 图像拦截和 NeMo Jailbreak 检测，可通过 `details.rule` 字段区分来源。

### 6.6 数据存储与资源错误 (5xxx)

| 错误码 | HTTP | type | 默认消息 | 触发场景 |
|--------|------|------|---------|---------|
| E5001 | 500 | `server_error` | 数据库操作失败 | PostgreSQL 连接异常或查询失败 |
| E5002 | 500 | `server_error` | 缓存服务不可用 | Redis 连接异常 |
| E5003 | 500 | `server_error` | 事务执行失败，操作已回滚 | 跨数据源操作失败 |
| E5004 | 507 | `resource_error` | GPU 显存不足，无法加载模型 | 加载模型时显存不够 |
| E5005 | 503 | `unavailable_error` | GPU 设备不可用，无法处理推理请求 | GPU 驱动异常或设备不可达 |

### 6.7 文档解析错误 (6xxx)

| 错误码 | HTTP | type | 默认消息 | 触发场景 | details 示例 |
|--------|------|------|---------|---------|-------------|
| E6001 | 413 | `invalid_request_error` | 文件大小超过限制（最大 10MB） | 上传文件超过 10MB | `{"file_size": 15000000, "max_size": 10485760}` |
| E6002 | 400 | `invalid_request_error` | 不支持的文件格式 | MIME 类型不在白名单中 | `{"mime_type": "image/png"}` |
| E6003 | 400 | `invalid_request_error` | 文件已损坏，无法解析 | 文件头校验失败或解析异常 | - |
| E6004 | 400 | `invalid_request_error` | 不支持加密文件 | PDF/DOCX 有密码保护 | - |
| E6005 | 400 | `invalid_request_error` | 该格式已列入支持计划，当前暂未启用 | 白名单内但尚未实现的格式 | `{"file_type": "pptx"}` |
| E6006 | 400 | `invalid_request_error` | 文档页数超过限制（最大 200 页） | PDF 超过 200 页 | `{"page_count": 350, "max_pages": 200}` |
| E6007 | 400 | `invalid_request_error` | 页码超出文档范围 | `pages` 参数指定的页码不存在 | - |
| E6008 | 503 | `unavailable_error` | 解析任务队列已满，请稍后重试 | 并发任务数超过 50 | `{"queue_size": 50, "max_size": 50}` |
| E6009 | 410 | `invalid_request_error` | 任务已过期（超过 7 天） | 查询已创建 7 天以上的任务 | `{"task_id": "xxx"}` |

### 6.8 HTTP 状态码速查

| HTTP | 语义 | 对应错误码 |
|------|------|-----------|
| 200 | 成功 | - |
| 202 | 任务已接受（异步） | - |
| 400 | 请求参数错误 | E1001, E3004, E6002-E6007 |
| 401 | 认证失败 | E2001, E2003 |
| 403 | 安全拦截 / 权限不足 | E2002, E4001-E4005 |
| 404 | 资源不存在 | E3001 |
| 409 | 冲突（任务未完成） | TASK_NOT_COMPLETED |
| 410 | 资源已过期 | E6009 |
| 413 | 文件过大 | E6001 |
| 429 | 频率/配额限制 | E1002, E2004 |
| 500 | 服务器内部错误 | E1000, E5001-E5003 |
| 503 | 服务不可用 | E3002, E5005, E6008 |
| 504 | 网关超时 | E3003 |
| 507 | 存储不足（显存） | E5004 |

---

## 7. 流式响应协议

### 7.1 SSE 基本格式

流式响应使用 **Server-Sent Events (SSE)** 协议：

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Request-ID: req-xxx
```

每个数据块格式：

```
data: {JSON}\n\n
```

结束标记：

```
data: [DONE]\n\n
```

### 7.2 标准内容 Chunk

```json
data: {"id":"chatcmpl-req12345678","object":"chat.completion.chunk","created":1707825600,"model":"qwen3-8b","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-req12345678","object":"chat.completion.chunk","created":1707825600,"model":"qwen3-8b","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-req12345678","object":"chat.completion.chunk","created":1707825600,"model":"qwen3-8b","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}

data: {"id":"chatcmpl-req12345678","object":"chat.completion.chunk","created":1707825600,"model":"qwen3-8b","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**关键行为**:

- 第一个 chunk 的 `delta` 包含 `role: "assistant"`
- 后续 chunk 的 `delta` 仅包含 `content`（增量文本）
- 最后一个 chunk 的 `finish_reason` 为非 null 值（`stop`/`length`/`tool_calls`）
- `[DONE]` 标记流式结束

### 7.3 安全控制事件

当输出安全护栏在流式过程中触发时，会发送特殊的 `content_control` 事件：

```
event: content_control
data: {"type":"clear_previous","reason":"safety_violation","replacement":"内容已被安全策略过滤"}
```

**上游处理**:

- 收到 `event: content_control` 后，**丢弃之前所有已接收的内容**
- 使用 `replacement` 字段值作为最终显示内容

### 7.4 流式响应中的护栏元数据

当 `guardrails_level=deep` 且触发了软拦截警告，或设置了 `include_usage=true` 时：

在所有内容 chunk 发送完毕后，最后一个标准 chunk 中附加 `warnings` 和 `guardrails_metadata` 字段（与 `choices` 并列的顶层字段）：

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1707825600,"model":"qwen3-8b","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"warnings":[{"type":"hallucination_detected","confidence":0.85,"details":"..."}],"guardrails_metadata":{"activated_rails":["input:jailbreak","output:hallucination"],"timings":{"input:jailbreak":0.048,"output:hallucination":2.35},"warnings_triggered":true,"total_duration":2.40}}

data: [DONE]
```

这些字段作为 JSON 向前兼容的可选扩展，不影响标准 OpenAI SSE 解析器。

### 7.5 流式响应中的错误

流式过程中如果发生异常，错误以 SSE data 事件发送：

```
data: {"error":{"code":"E1000","message":"服务器内部错误","type":"server_error","request_id":"req-xxx"}}

data: [DONE]
```

### 7.6 流式输出安全缓冲

`guardrails_level=standard` 或 `deep` 时，流式响应的安全检查采用**先缓冲后发送**策略：

1. 前 128KB 内容在服务端缓冲，**不立即发送**
2. 缓冲区满后执行 Output Rails 检查
3. 检查通过 → 已缓冲内容和后续内容流式返回
4. 检查拦截（硬拦截）→ 返回 403，客户端不会收到任何内容
5. 超过 128KB 的后续内容不再检查，直接流式返回

> **对上游的影响**: 启用 `standard` 或 `deep` 护栏时，首个 chunk 的到达时间可能增加（需等待 Output Rails 检查完成）。`fast` 级别不受影响。

---

## 8. 对接最佳实践

### 8.1 重试策略

建议上游应用实现如下重试策略：

| HTTP 状态码 | 是否重试 | 策略 |
|------------|---------|------|
| 429 | 是 | 指数退避，最少等 5 秒 |
| 500 | 是 | 指数退避，最多 3 次 |
| 502/503/504 | 是 | 指数退避 + 随机抖动，检查 `retry_after` |
| 400/401/403/404 | **否** | 客户端错误，修正请求后重试 |
| 507 | **否** | 显存不足，等待模型卸载后手动重试 |

**推荐退避公式**: `wait = min(base * 2^attempt + random(0, 1), max_wait)`

- `base` = 1 秒
- `max_wait` = 30 秒
- 最多重试 3 次

### 8.2 超时配置建议

| 场景 | 建议超时 | 说明 |
|------|---------|------|
| 聊天推理（非流式） | 60s | 包含模型热切换时间 |
| 聊天推理（流式首 chunk） | 30s | 首 token 到达时间 |
| 聊天推理（流式后续） | 10s/chunk | 两个 chunk 间的最大间隔 |
| 深度护栏推理 | 90s | 包含 LLM 推理 + 双重护栏检查 |
| 文档解析（创建任务） | 30s | 含文件上传和校验 |
| 文本向量化 | 30s | - |
| 模型加载 | 120s | 模型文件加载到 GPU |

### 8.3 护栏级别选择建议

| 场景 | 推荐级别 | 理由 |
|------|---------|------|
| 内部工具/脚本 | `fast` | 低延迟，基本防护已够 |
| 面向用户的聊天 | `standard` | 防越狱和注入攻击 |
| 金融/医疗/法律问答 | `deep` | 需要幻觉检测和事实核查 |
| 自动化测试 | `guardrails_enabled=false` | 跳过护栏加速测试 |

### 8.4 文档解析对接注意事项

1. **轮询间隔**: 建议 2-5 秒，避免频繁请求
2. **大文件策略**: 超过 50 页的文件建议使用 `pages` 参数分批解析
3. **模型选择**:
   - 通用 OCR → `qwen2.5-vl`
   - 短文档（≤5 页）→ `minicpm-v`（多图推理更快）
   - 含音视频的多模态文档 → `minicpm-o`
4. **结果缓存**: 同一文件使用同一模型的结果会被缓存 7 天，重复上传会命中缓存
5. **任务生命周期**: 任务及结果在 7 天后自动清理，请及时获取结果

### 8.5 OpenAI SDK 兼容用法

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-23h8ugn3828910h8g308979y4",
    base_url="http://127.0.0.1:8100/v1",
)

# 非流式
response = client.chat.completions.create(
    model="qwen3-8b",
    messages=[{"role": "user", "content": "你好"}],
    temperature=0.7,
    max_tokens=512,
)
print(response.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="qwen3-8b",
    messages=[{"role": "user", "content": "讲一个故事"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")

# Tool Calling
response = client.chat.completions.create(
    model="qwen3-8b",
    messages=[{"role": "user", "content": "北京天气"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]
            }
        }
    }],
)

# Embeddings
response = client.embeddings.create(
    model="qwen3-embedding",
    input=["文本一", "文本二"],
)
```

### 8.6 Python httpx 直接调用

```python
import httpx

# 注意：WSL2 环境需配置代理
headers = {
    "Authorization": "Bearer sk-23h8ugn3828910h8g308979y4",
    "Content-Type": "application/json",
}

# 非流式
response = httpx.post(
    "http://127.0.0.1:8100/v1/chat/completions",
    headers=headers,
    json={
        "model": "qwen3-8b",
        "messages": [{"role": "user", "content": "你好"}],
    },
    timeout=60,
)
print(response.json())

# 文档解析
with open("report.pdf", "rb") as f:
    response = httpx.post(
        "http://127.0.0.1:8100/v1/documents/parse",
        headers={"Authorization": "Bearer sk-23h8ugn3828910h8g308979y4"},
        files={"file": ("report.pdf", f, "application/pdf")},
        data={"model": "qwen2.5-vl"},
        timeout=30,
    )
task_id = response.json()["task_id"]
```

---

## 附录 A: 护栏执行链路详细说明

### A.1 完整护栏链（guardrails_level=deep）

```
请求进入
  │
  ▼ ① 自研规则引擎 Input 检查 (<10ms)
  ├── PII 检测 → E4002 (403)
  ├── Prompt 注入检测 → E4001 (403)
  ├── 有害内容检测 → E4003 (403)
  └── 通过 ↓

  ▼ ② NeMo Input Rails (<70ms)
  ├── Jailbreak Detection（困惑度启发式）→ E4004 (403)
  ├── YARA 规则引擎（SQL/XSS/命令注入）→ E4005 (403)
  └── 通过 ↓

  ▼ ③ LLM 推理

  ▼ ④ NeMo Output Rails (<30s)
  ├── Self-Check Hallucination → 软拦截 (warnings)
  ├── Self-Check Facts (Tavily 网络搜索) → 软拦截 (warnings)
  └── 完成 ↓

  ▼ ⑤ 返回响应（可能包含 warnings + guardrails_metadata）
```

### A.2 降级链路

```
外部 LLM (external mode) 失败
  → 切换到本地 LLM (继续 NeMo 护栏)
    → 本地 LLM 失败
      → 降级到自研规则引擎
        → 自研引擎失败
          → Fail-Open 放行 (不阻塞请求，审计记录)
```

---

## 附录 B: 变更日志

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| 2.0.0 | 2026-02-11 | 新增 NeMo Guardrails 混合护栏：`guardrails_enabled`、`guardrails_level`、`include_usage` 参数；新增 E4004/E4005 错误码；新增 `warnings`/`guardrails_metadata` 响应字段；新增流式输出安全缓冲策略 |
| 1.4.0 | 2026-02-08 | 多 VL 模型支持：`model` 参数从可选改为必填（破坏性变更）；新增 minicpm-v/minicpm-o |
| 1.3.0 | 2026-02-05 | VL 文档解析服务：文档解析三端点、异步任务模式、6xxx 错误码 |
| 1.2.0 | 2026-01-30 | 动态模型加载器：模型管理端点、热切换、Sleep/Wake |
| 1.1.0 | 2026-01-28 | Tool Calling 全链路、思维链控制 |
| 1.0.0 | 2026-01-25 | MVP：聊天推理、安全护栏、审计日志 |
