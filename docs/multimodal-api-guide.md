# LLM Gateway 多模态文档解析 API 对接指引

> **版本**: 1.0.0
> **更新日期**: 2026-02-10
> **适用范围**: 上游应用集成 LLM Gateway 文档解析接口

---

## 目录

- [一、功能概述](#一功能概述)
- [二、认证方式](#二认证方式)
- [三、API 端点列表](#三api-端点列表)
- [四、对接流程](#四对接流程)
- [五、接口详细说明](#五接口详细说明)
- [六、错误码参考](#六错误码参考)
- [七、请求示例（完整代码）](#七请求示例完整代码)
- [八、注意事项](#八注意事项)
- [九、FAQ](#九faq)

---

## 一、功能概述

LLM Gateway 提供基于视觉语言模型（VL Model）的智能文档解析服务，可将 **PDF / DOCX** 文件转换为结构化 **Markdown** 文本。

### 核心能力

| 能力 | 说明 |
|------|------|
| 文件格式 | PDF、DOCX |
| 输出格式 | Markdown 纯文本 或 JSON（逐页详情） |
| 异步解析 | 提交任务后立即返回任务 ID，后续轮询获取结果 |
| 指定页码 | 支持只解析指定页，如 `1,3-5,8` |
| 多 VL 模型 | 支持 qwen2.5-vl / minicpm-v / minicpm-o，按需选择 |
| 短文档多图推理 | minicpm-v 对 ≤5 页短文档一次推理多页，速度更快 |
| 缓存 | 同一文件 + 同一模型的解析结果 7 天内缓存复用 |

### 处理流程

```
上传文件  ──→  创建任务（202）  ──→  后台解析  ──→  轮询进度  ──→  获取结果
  POST            task_id              异步           GET            GET
```

---

## 二、认证方式

所有请求必须携带 API Key：

```
Authorization: Bearer <your-api-key>
```

**示例：**

```bash
curl -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" ...
```

请求追踪 ID（可选）：

```
X-Request-ID: <your-trace-id>
```

未传时网关自动生成 UUID 并在响应头返回，建议传入以便全链路追踪。

---

## 三、API 端点列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/documents/parse` | POST | 上传文件，创建解析任务 |
| `/v1/documents/tasks/{task_id}` | GET | 查询任务状态和进度 |
| `/v1/documents/tasks/{task_id}/result` | GET | 获取解析结果 |

**Base URL 示例：**

```
# 内网
http://<gateway-host>:8081

# 公网（如已配置穿透）
http://120.25.192.185:8100
```

---

## 四、对接流程

### 4.1 标准三步流程

```
步骤 1: POST /v1/documents/parse
        ↓ 返回 task_id (HTTP 202)
步骤 2: GET /v1/documents/tasks/{task_id}
        ↓ 轮询直到 status == "completed" 或 "failed"
步骤 3: GET /v1/documents/tasks/{task_id}/result
        ↓ 获取 Markdown 或 JSON 格式结果
```

### 4.2 推荐轮询策略

```python
import time

MAX_POLL = 120    # 最多轮询 120 次
INTERVAL = 3      # 每 3 秒轮询一次（短文档可缩短到 1 秒）

for i in range(MAX_POLL):
    resp = requests.get(f"{BASE}/v1/documents/tasks/{task_id}", headers=headers)
    data = resp.json()

    if data["status"] == "completed":
        break
    elif data["status"] == "failed":
        raise Exception(f"解析失败: {data.get('error_message')}")

    # 可根据进度动态调整间隔
    progress = data.get("progress", {})
    print(f"进度: {progress.get('current', 0)}/{progress.get('total', 0)}")
    time.sleep(INTERVAL)
```

> **建议**：简单文件 1-3 页约 10-30 秒完成；复杂文件 50+ 页可能需要数分钟。

---

## 五、接口详细说明

### 5.1 创建解析任务

```
POST /v1/documents/parse
Content-Type: multipart/form-data
```

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | **是** | 上传的 PDF 或 DOCX 文件 |
| `model` | string | **是** | VL 模型 ID（见下方模型列表） |
| `pages` | string | 否 | 指定页码范围，如 `"1,3-5,8"`。不传则解析全部页 |

#### 可用 VL 模型

| model 值 | 模型全称 | 特点 | 推荐场景 |
|----------|----------|------|----------|
| `qwen2.5-vl` | Qwen2.5-VL-7B | 中文 OCR 能力强，上下文窗口大（32K） | 中文文档首选 |
| `minicpm-v` | MiniCPM-V 4.5 | 支持多图推理（≤5 页一次推理） | 短文档快速解析 |
| `minicpm-o` | MiniCPM-o 4.5 | 多模态增强（音视频能力） | 特殊多模态需求 |

> **注意**: `minicpm-v` 和 `minicpm-o` 共用 GPU 端口，同时只能运行其中一个。请求某个模型时，如果当前加载的是另一个，网关会自动热切换（约 30-120 秒）。

#### 页码范围语法

| 写法 | 含义 |
|------|------|
| `"1"` | 仅第 1 页 |
| `"1,3,5"` | 第 1、3、5 页 |
| `"2-8"` | 第 2 到 8 页 |
| `"1,3-5,8"` | 第 1、3、4、5、8 页 |
| 不传 | 解析全部页 |

页码从 **1** 开始。超出文档范围的页码会返回错误。

#### 成功响应 (HTTP 202)

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "model": "qwen2.5-vl",
  "file_name": "年度报告.pdf",
  "file_type": "pdf",
  "total_pages": 42,
  "requested_pages": "1-10",
  "progress": {
    "current": 0,
    "total": 10
  },
  "error_message": null,
  "created_at": "2026-02-10T08:30:00+00:00",
  "completed_at": null
}
```

#### curl 示例

```bash
curl -X POST "http://localhost:8081/v1/documents/parse" \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@/path/to/document.pdf" \
  -F "model=qwen2.5-vl" \
  -F "pages=1-10"
```

---

### 5.2 查询任务状态

```
GET /v1/documents/tasks/{task_id}
```

#### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | string | 是 | 创建任务时返回的 UUID |

#### 任务状态流转

```
pending  ──→  processing  ──→  completed
                   │
                   └──→  failed
```

| 状态 | 含义 | 下一步操作 |
|------|------|-----------|
| `pending` | 排队中，等待处理 | 继续轮询 |
| `processing` | 解析中，可查看进度 | 继续轮询，关注 `progress` 字段 |
| `completed` | 解析完成 | 调用结果接口获取 Markdown |
| `failed` | 解析失败 | 查看 `error_message`，处理错误 |

#### 响应示例（处理中）

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "processing",
  "model": "qwen2.5-vl",
  "file_name": "年度报告.pdf",
  "file_type": "pdf",
  "total_pages": 42,
  "requested_pages": "1-10",
  "progress": {
    "current": 6,
    "total": 10
  },
  "error_message": null,
  "created_at": "2026-02-10T08:30:00+00:00",
  "completed_at": null
}
```

#### 响应示例（失败）

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "failed",
  "model": "minicpm-v",
  "file_name": "damaged.pdf",
  "file_type": "pdf",
  "total_pages": 0,
  "requested_pages": null,
  "progress": {
    "current": 0,
    "total": 0
  },
  "error_message": "GPU 显存不足，请释放资源后重试",
  "created_at": "2026-02-10T08:30:00+00:00",
  "completed_at": "2026-02-10T08:31:15+00:00"
}
```

---

### 5.3 获取解析结果

```
GET /v1/documents/tasks/{task_id}/result?format=markdown
```

#### 查询参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `format` | string | 否 | `markdown` | 返回格式：`markdown` 或 `json` |

#### Markdown 格式响应 (Content-Type: text/markdown)

```markdown
<!-- Page 1 -->

# 2025 年度报告

## 一、公司概况

本公司成立于 2010 年...

---

<!-- Page 2 -->

## 二、财务摘要

| 指标 | 2024 | 2025 |
|------|------|------|
| 营收（亿元） | 52.3 | 68.7 |
| 净利润（亿元） | 8.1 | 12.4 |

[此处为柱状图，展示 2024-2025 年各季度营收对比]
```

**Markdown 格式说明：**

- 每页以 `<!-- Page N -->` 注释标记分隔（N 从 1 开始）
- 页与页之间用 `---` 分割线隔开
- 表格使用标准 Markdown 表格语法
- 图片/图表以 `[文字描述]` 形式呈现（VL 模型不提取图片文件）
- 空白页标记为 `<!-- 空白页 -->`

#### JSON 格式响应 (Content-Type: application/json)

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "model": "qwen2.5-vl",
  "total_pages": 3,
  "pages": [
    {
      "page_number": 1,
      "markdown": "# 标题\n\n正文内容...",
      "confidence": null,
      "latency_ms": 3245.8
    },
    {
      "page_number": 2,
      "markdown": "| 列1 | 列2 |\n|---|---|\n| A | B |",
      "confidence": null,
      "latency_ms": 4102.3
    },
    {
      "page_number": 3,
      "markdown": "<!-- 空白页 -->",
      "confidence": null,
      "latency_ms": 1205.1
    }
  ],
  "usage": {
    "total_prompt_tokens": 18500,
    "total_completion_tokens": 6200,
    "total_tokens": 24700
  },
  "total_latency_ms": 8553.2
}
```

#### 任务未完成时请求结果 (HTTP 409)

```json
{
  "error": {
    "code": "TASK_NOT_COMPLETED",
    "message": "任务尚未完成",
    "type": "conflict",
    "details": {
      "status": "processing",
      "progress": {
        "current": 3,
        "total": 10
      }
    }
  }
}
```

> 收到 409 时应继续轮询状态接口，等待 `completed` 后再请求结果。

---

## 六、错误码参考

### 文档解析专用错误码 (E6xxx)

| 错误码 | HTTP | 含义 | 处理建议 |
|--------|------|------|----------|
| `E6001` | 413 | 文件过大（超过 10MB） | 压缩文件或拆分后重新上传 |
| `E6002` | 400 | 不支持的文件格式 | 仅支持 PDF 和 DOCX |
| `E6003` | 400 | 文件已损坏 | 检查文件完整性 |
| `E6004` | 400 | 文件已加密/密码保护 | 去除密码后重新上传 |
| `E6005` | 400 | 格式暂未启用 | 当前请使用 PDF 格式 |
| `E6006` | 400 | 页数超过限制（>200 页） | 使用 `pages` 参数指定需要的页码范围 |
| `E6007` | 400 | 页码范围无效 | 检查 `pages` 参数格式或页码是否超出文档范围 |
| `E6008` | 503 | 任务队列已满（>50 个任务） | 稍后重试 |
| `E6009` | 410 | 任务已过期（>7 天） | 重新提交解析任务 |

### 通用错误码

| 错误码 | HTTP | 含义 | 处理建议 |
|--------|------|------|----------|
| `E2001` | 401 | 认证失败 | 检查 API Key |
| `E3001` | 404 | 模型不存在 / 非 VL 模型 | 查看响应中的 `available_vl_models` 列表 |
| `E3002` | 503 | 解析服务未就绪 | 网关启动中或模型未加载，稍后重试 |

### 统一错误响应格式

```json
{
  "error": {
    "code": "E6001",
    "message": "文件大小超过限制（最大 10MB）",
    "type": "invalid_request_error",
    "request_id": "req-uuid-xxxxx",
    "details": {
      "file_size": 15728640,
      "max_size": 10485760
    }
  }
}
```

### 非 VL 模型错误响应示例

请求非 VL 模型进行文档解析时：

```json
{
  "error": {
    "code": "E3001",
    "message": "模型 qwen3-8b 不支持文档解析，请使用 VL 模型",
    "type": "not_found_error",
    "details": {
      "available_vl_models": ["qwen2.5-vl", "minicpm-v", "minicpm-o"]
    }
  }
}
```

---

## 七、请求示例（完整代码）

### 7.1 Python (requests)

```python
import requests
import time

BASE_URL = "http://localhost:8081"
API_KEY = "sk-23h8ugn3828910h8g308979y4"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def parse_document(file_path: str, model: str = "qwen2.5-vl",
                   pages: str | None = None) -> str:
    """
    解析文档并返回 Markdown 文本。

    Args:
        file_path: 文件路径（PDF 或 DOCX）
        model: VL 模型 ID（qwen2.5-vl / minicpm-v / minicpm-o）
        pages: 可选页码范围，如 "1,3-5"

    Returns:
        Markdown 格式的解析结果
    """

    # 步骤 1: 上传文件，创建解析任务
    with open(file_path, "rb") as f:
        form_data = {"model": model}
        if pages:
            form_data["pages"] = pages

        resp = requests.post(
            f"{BASE_URL}/v1/documents/parse",
            headers=HEADERS,
            files={"file": (file_path.split("/")[-1], f)},
            data=form_data,
        )

    if resp.status_code != 202:
        raise Exception(f"创建任务失败: {resp.status_code} {resp.json()}")

    task_id = resp.json()["task_id"]
    total = resp.json()["progress"]["total"]
    print(f"任务已创建: {task_id}，共 {total} 页")

    # 步骤 2: 轮询任务状态
    for _ in range(300):  # 最多等 15 分钟
        resp = requests.get(
            f"{BASE_URL}/v1/documents/tasks/{task_id}",
            headers=HEADERS,
        )
        data = resp.json()
        status = data["status"]
        progress = data.get("progress", {})

        if status == "completed":
            print(f"解析完成！{progress.get('current')}/{progress.get('total')} 页")
            break
        elif status == "failed":
            raise Exception(f"解析失败: {data.get('error_message')}")
        else:
            current = progress.get("current", 0)
            total = progress.get("total", 0)
            print(f"[{status}] 进度: {current}/{total}")
            time.sleep(3)
    else:
        raise TimeoutError("轮询超时")

    # 步骤 3: 获取 Markdown 结果
    resp = requests.get(
        f"{BASE_URL}/v1/documents/tasks/{task_id}/result",
        headers=HEADERS,
        params={"format": "markdown"},
    )

    if resp.status_code == 200:
        return resp.text
    else:
        raise Exception(f"获取结果失败: {resp.status_code} {resp.json()}")


# 使用示例
if __name__ == "__main__":
    # 基本用法：解析整个 PDF
    markdown = parse_document("report.pdf", model="qwen2.5-vl")
    print(markdown)

    # 只解析前 5 页
    markdown = parse_document("report.pdf", model="qwen2.5-vl", pages="1-5")

    # 使用 minicpm-v（短文档多图推理更快）
    markdown = parse_document("invoice.pdf", model="minicpm-v")

    # 解析 DOCX
    markdown = parse_document("contract.docx", model="qwen2.5-vl")
```

### 7.2 Python (httpx 异步)

```python
import httpx
import asyncio

BASE_URL = "http://localhost:8081"
HEADERS = {"Authorization": "Bearer sk-23h8ugn3828910h8g308979y4"}


async def parse_document_async(file_path: str, model: str = "qwen2.5-vl",
                                pages: str | None = None) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 步骤 1: 创建任务
        with open(file_path, "rb") as f:
            data = {"model": model}
            if pages:
                data["pages"] = pages

            resp = await client.post(
                f"{BASE_URL}/v1/documents/parse",
                headers=HEADERS,
                files={"file": (file_path.split("/")[-1], f, "application/pdf")},
                data=data,
            )

        assert resp.status_code == 202, f"创建失败: {resp.text}"
        task_id = resp.json()["task_id"]

        # 步骤 2: 轮询
        for _ in range(300):
            resp = await client.get(
                f"{BASE_URL}/v1/documents/tasks/{task_id}",
                headers=HEADERS,
            )
            data = resp.json()
            if data["status"] == "completed":
                break
            elif data["status"] == "failed":
                raise Exception(data.get("error_message"))
            await asyncio.sleep(3)

        # 步骤 3: 获取结果
        resp = await client.get(
            f"{BASE_URL}/v1/documents/tasks/{task_id}/result",
            headers=HEADERS,
            params={"format": "markdown"},
        )
        return resp.text


# 并发解析多个文件
async def batch_parse(files: list[str], model: str = "qwen2.5-vl"):
    tasks = [parse_document_async(f, model) for f in files]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return results
```

### 7.3 curl

```bash
# ---------- 步骤 1: 创建解析任务 ----------
curl -X POST "http://localhost:8081/v1/documents/parse" \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4" \
  -F "file=@report.pdf" \
  -F "model=qwen2.5-vl"

# 响应:
# {"task_id": "a1b2c3d4-...", "status": "pending", ...}

# ---------- 步骤 2: 查询状态 ----------
curl "http://localhost:8081/v1/documents/tasks/a1b2c3d4-..." \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"

# ---------- 步骤 3a: 获取 Markdown 结果 ----------
curl "http://localhost:8081/v1/documents/tasks/a1b2c3d4-.../result?format=markdown" \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"

# ---------- 步骤 3b: 获取 JSON 结果（含 token 用量和耗时） ----------
curl "http://localhost:8081/v1/documents/tasks/a1b2c3d4-.../result?format=json" \
  -H "Authorization: Bearer sk-23h8ugn3828910h8g308979y4"
```

### 7.4 JavaScript (fetch)

```javascript
const BASE_URL = "http://localhost:8081";
const API_KEY = "sk-23h8ugn3828910h8g308979y4";

async function parseDocument(file, model = "qwen2.5-vl", pages = null) {
  const headers = { Authorization: `Bearer ${API_KEY}` };

  // 步骤 1: 创建任务
  const formData = new FormData();
  formData.append("file", file);
  formData.append("model", model);
  if (pages) formData.append("pages", pages);

  let resp = await fetch(`${BASE_URL}/v1/documents/parse`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (resp.status !== 202) {
    throw new Error(`创建任务失败: ${await resp.text()}`);
  }

  const { task_id } = await resp.json();

  // 步骤 2: 轮询状态
  while (true) {
    resp = await fetch(`${BASE_URL}/v1/documents/tasks/${task_id}`, { headers });
    const data = await resp.json();

    if (data.status === "completed") break;
    if (data.status === "failed") throw new Error(data.error_message);

    await new Promise((r) => setTimeout(r, 3000));
  }

  // 步骤 3: 获取结果
  resp = await fetch(
    `${BASE_URL}/v1/documents/tasks/${task_id}/result?format=markdown`,
    { headers }
  );
  return await resp.text();
}
```

---

## 八、注意事项

### 8.1 文件限制

| 限制项 | 值 | 说明 |
|--------|------|------|
| 文件大小 | **≤ 10MB** | 超过返回 413 |
| 文件格式 | PDF / DOCX | 其他格式返回 400 |
| 文档页数 | **≤ 200 页** | 超过返回 400，建议用 `pages` 参数分批解析 |
| 加密文件 | 不支持 | 密码保护的 PDF 返回 400 |
| 并发队列 | **≤ 50 个任务** | 队列满时返回 503 |

### 8.2 model 参数为必填

`model` 参数是**强制必填**的（无默认值）。不传 `model` 会返回 `422 Validation Error`。

```bash
# ❌ 错误: 缺少 model 参数
curl -X POST ".../v1/documents/parse" -F "file=@doc.pdf"

# ✅ 正确: 指定 model
curl -X POST ".../v1/documents/parse" -F "file=@doc.pdf" -F "model=qwen2.5-vl"
```

### 8.3 模型选择建议

| 场景 | 推荐模型 | 理由 |
|------|----------|------|
| 中文文档 | `qwen2.5-vl` | 中文 OCR 能力最强，32K 上下文 |
| 英文文档 | `minicpm-v` | 英文 Prompt，短文档多图推理更快 |
| 短文档（≤5 页） | `minicpm-v` | 多图推理一次处理多页，速度优势明显 |
| 长文档（>5 页） | `qwen2.5-vl` | 逐页解析，质量更稳定 |
| 混合语言文档 | `qwen2.5-vl` | 中文 Prompt 也能处理英文内容 |

### 8.4 模型热切换等待

`minicpm-v` 和 `minicpm-o` 共用 GPU 端口 8006，同一时刻只能运行一个。当请求的模型不是当前加载的模型时，网关会自动触发热切换：

- 热切换耗时约 30-120 秒
- 期间任务状态为 `processing`，进度不推进
- 超过 120 秒切换失败，任务标记为 `failed`
- **建议**：尽量让同类请求集中使用同一模型，减少切换次数

### 8.5 结果缓存机制

- 同一文件（按 SHA-256 hash）+ 同一模型的解析结果会缓存 **7 天**
- 不同模型对同一文件的结果**互相独立**，不会互相覆盖
- 缓存命中时直接返回结果，不会重新推理
- 修改文件内容后（hash 变化），会重新解析

### 8.6 任务过期清理

- 任务创建 **7 天后**自动过期
- 过期后查询状态返回 `410 Gone`
- 过期后需重新提交解析任务
- 建议在任务完成后及时拉取结果并本地持久化

### 8.7 断线续查

- 客户端断开连接**不影响**后台解析任务
- 只要保存了 `task_id`，可随时重新轮询状态和获取结果
- 适合移动端等不稳定网络环境

### 8.8 Markdown 输出特点

| 元素 | 处理方式 |
|------|----------|
| 标题 | `# ## ###` 等层级保留 |
| 表格 | 标准 Markdown 表格语法 |
| 列表 | 有序/无序列表语法 |
| 图片/图表 | `[文字描述]` 形式（不提取图片文件） |
| 空白页 | `<!-- 空白页 -->` 注释 |
| 页分隔 | `<!-- Page N -->` 注释 + `---` 分割线 |
| 渲染失败页 | `<!-- 渲染超时，已跳过 -->` |
| 解析失败页 | `<!-- 解析失败: 错误原因 -->` |

### 8.9 GPU 显存不足处理

当 GPU 显存使用率超过 99% 时：

1. 新任务会延迟执行（重入队列等待 30 秒）
2. 最多重试 10 次
3. 超过后任务标记为 `failed`，`error_message` 提示显存不足
4. **处理建议**：等待其他任务完成后重试，或切换到显存占用较小的模型

---

## 九、FAQ

### Q: DOCX 文件是如何处理的？

A: DOCX 会先通过 LibreOffice (headless) 转换为 PDF，再按 PDF 流程逐页渲染图像并交给 VL 模型推理。转换本身对排版有一定影响，建议优先使用 PDF。

### Q: 能解析扫描版 PDF 吗？

A: 可以。VL 模型本身就是对页面图像进行视觉理解，不依赖 PDF 内的文字层。扫描版 PDF 和文字版 PDF 走相同流程。

### Q: 解析结果中的图片怎么处理？

A: 文档中的图片由 VL 模型以文字描述形式输出，如 `[此处为柱状图，展示2024年各季度营收]`。不会提取或返回原始图片文件。

### Q: 多图推理和逐页推理有什么区别？

A: `minicpm-v` 对 ≤5 页的短文档支持多图推理——将所有页面图像一次性发送给模型，减少推理次数。如果多图输出拆分失败（如模型未按页分隔），会自动回退到逐页推理。对上游调用方透明，无需额外处理。

### Q: 同一文件重复提交会怎样？

A: 会创建新的任务 ID，但解析时命中页面缓存（同模型 + 同文件 hash），直接返回缓存结果，速度极快。

### Q: 任务失败了可以重试吗？

A: 可以。直接重新提交 `POST /v1/documents/parse` 即可，会生成新的 `task_id`。

### Q: 如何判断模型是否可用？

A: 调用 `GET /v1/models` 可查看所有模型的状态。VL 模型状态为 `running` 时可直接使用；状态为 `unloaded` 或 `sleeping` 时，提交解析任务会触发自动加载/唤醒（需等待）。

---

> 如有问题，请联系网关服务运维团队。
