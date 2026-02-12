# Research: 全模态模型接入 (MiniCPM-V/o)

**Feature Branch**: `008-multimodal-minicpm`
**Date**: 2026-02-06

## 1. 媒体存储方案

### Decision: MinIO S3 兼容存储

**选择**: 使用已部署的 MinIO 服务存储媒体文件

**Rationale**:
- 项目已部署 MinIO (端口 9010/9011)，无需额外基础设施
- S3 兼容 API，可使用成熟的 boto3 客户端
- 支持预签名 URL，可控制访问权限和过期时间
- 支持生命周期策略，可自动清理过期文件

**Alternatives Considered**:
- 本地文件系统: 不支持分布式，扩展性差
- 阿里云 OSS: 需要公网访问，增加成本和延迟
- PostgreSQL BLOB: 不适合大文件存储，影响数据库性能

**Implementation Notes**:
```python
# MinIO 配置
MINIO_ENDPOINT = "localhost:9010"
MINIO_ACCESS_KEY = env("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = env("MINIO_SECRET_KEY")
MINIO_BUCKET_MEDIA = "linchat-media"

# 存储路径结构
# 原始文件: media/{user_id}/{YYYY-MM-DD}/{uuid}.{ext}
```

## 2. ~~缩略图生成方案~~ [已废弃]

> **注意**：后续设计决策中移除了后端缩略图生成，改为前端静态 SVG 占位图方案。Pillow 仍用于图片元数据提取，ffmpeg-python 仍用于视频时长检测。以下为原始研究记录，仅供参考。

### Decision: ~~Pillow + ffmpeg-python（已废弃）~~

**原方案**: 图片使用 Pillow，视频使用 ffmpeg-python 生成缩略图

**废弃原因**: 后端生成缩略图属于过度设计，前端使用静态 SVG 占位图即可满足需求

**Alternatives Considered**:
- ImageMagick (wand): 功能强大但依赖复杂
- OpenCV: 偏向计算机视觉，缩略图生成大材小用
- moviepy: 基于 FFmpeg 但 API 更高层，灵活性不足

**Implementation Notes**:
```python
# 图片缩略图 (Pillow)
from PIL import Image

def generate_image_thumbnail(input_path: str, output_path: str) -> None:
    with Image.open(input_path) as img:
        img.thumbnail((200, 200), Image.Resampling.LANCZOS)
        img = img.convert("RGB")  # 确保 JPEG 兼容
        img.save(output_path, "JPEG", quality=80)

# 视频缩略图 (ffmpeg-python)
import ffmpeg

def generate_video_thumbnail(input_path: str, output_path: str) -> None:
    (
        ffmpeg
        .input(input_path, ss=1)  # 第 1 秒
        .filter("scale", 200, 200, force_original_aspect_ratio="decrease")
        .filter("pad", 200, 200, "(ow-iw)/2", "(oh-ih)/2")
        .output(output_path, vframes=1, format="image2")
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )
```

## 3. 推理任务状态管理

### Decision: Redis 临时状态 + EventService 机制

**选择**: 复用现有 EventService 和 Redis 存储推理任务状态

**Rationale**:
- 推理任务是临时状态，无需持久化到数据库
- EventService 已提供 Redis Pub/Sub 机制，可复用
- 按 user_id 粒度管理，符合宪法数据隔离要求
- 任务完成后自动清理，无需手动维护

**Alternatives Considered**:
- 数据库存储: 过度设计，增加不必要的 I/O
- 内存存储: 单机限制，重启丢失
- Celery 任务: 适合后台任务，不适合实时推理状态

**Implementation Notes**:
```python
# Redis 键结构
INFERENCE_TASK_KEY = "user:{user_id}:inference_task"
# 值: {"request_id": "uuid", "started_at": "iso_timestamp"}
# TTL: 300 秒（5分钟，防止僵尸任务）

# 扩展 EventType
class EventType(str, Enum):
    LOGOUT = "logout"
    MESSAGE = "message"
    HEARTBEAT = "heartbeat"
    CONTEXT_STATUS = "context_status"
    INFERENCE_CANCEL = "inference_cancel"  # 新增
```

## 4. ~~并发控制方案~~ [已废弃]

> **注意**：宪法 9.2 明确本项目为家庭场景单用户系统，禁止实现多用户并发控制机制。以下为原始研究记录，仅供参考。

### Decision: ~~Redis 分布式锁 + 用户选择弹窗（已废弃）~~

**原方案**: 使用 Redis 分布式锁实现单用户单推理限制，前端弹窗提示用户选择

**废弃原因**: 宪法 9.2 明确单用户场景不需要并发控制。用户通过"停止"按钮手动取消当前推理后再发送新请求

**Rationale**:
- Redis 分布式锁是项目已有模式，复用成熟方案
- 按 user_id 粒度锁定，符合宪法要求
- 弹窗让用户选择"等待"或"中断"，提供控制权
- 锁超时机制防止死锁

**Alternatives Considered**:
- 数据库行锁: 性能差，不适合高频操作
- 信号量: 本地限制，不支持分布式
- 自动排队: 用户体验差，无法控制

**Implementation Notes**:
```python
# 并发检查伪代码
async def check_inference_concurrency(user_id: int) -> InferenceTask | None:
    """检查用户是否有进行中的推理任务

    Returns:
        None 如果无进行中任务，可以开始新推理
        InferenceTask 如果有进行中任务，返回任务信息
    """
    key = f"user:{user_id}:inference_task"
    task_data = await redis.get(key)
    if task_data:
        return InferenceTask.from_json(task_data)
    return None

async def register_inference_task(user_id: int, request_id: str) -> bool:
    """注册新推理任务，使用 SETNX 保证原子性"""
    key = f"user:{user_id}:inference_task"
    task = {"request_id": request_id, "started_at": datetime.utcnow().isoformat()}
    # SETNX + TTL 防止死锁
    return await redis.set(key, json.dumps(task), nx=True, ex=300)
```

## 5. 多模态消息格式

### Decision: OpenAI 兼容格式

**选择**: 使用 OpenAI Vision API 兼容格式传递多模态内容

**Rationale**:
- LLM Gateway 已实现 OpenAI 兼容接口
- MiniCPM-V/o 支持 OpenAI 格式的多模态输入
- 无需自定义格式，降低对接复杂度
- 便于后续切换其他多模态模型

**Alternatives Considered**:
- 自定义格式: 增加维护成本
- Base64 内联: 消息体过大，不适合流式

**Implementation Notes**:
```python
# 多模态消息格式示例
{
    "role": "user",
    "content": [
        {"type": "text", "text": "描述这张图片"},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,{base64_data}"
                # 或使用 URL: "https://..."
            }
        }
    ]
}

# 视频消息（MiniCPM-V 扩展）
{
    "role": "user",
    "content": [
        {"type": "text", "text": "描述这段视频"},
        {
            "type": "video_url",
            "video_url": {
                "url": "https://...",
                "max_frames": 16  # 可选，限制采样帧数
            }
        }
    ]
}
```

## 6. 网关调用与超时处理

### Decision: httpx 异步客户端 + 分场景超时

> **注意**：研究阶段采用统一 120 秒超时。最终设计已改为按场景分别配置（FR-032）：推理 180s、取消 5s、轮询 30s、文档解析创建 30s、文档解析结果 30s、TTS 60s。以下代码示例中的超时值仅供参考。

**选择**: 使用 httpx 异步客户端调用网关，按场景配置超时（参见 FR-032 和 tasks.md T003）

**Rationale**:
- httpx 支持异步和流式响应，与 ASGI 架构匹配
- 分场景超时覆盖不同操作的延迟特征（推理 180s 含图像编码，取消 5s 要求快速响应）
- 流式响应使用 iter_lines() 逐行处理

**Alternatives Considered**:
- requests: 同步阻塞，不适合 ASGI
- aiohttp: 功能类似，httpx API 更简洁
- 30/60 秒超时: 可能导致长视频处理失败

**Implementation Notes**:
```python
import httpx

GATEWAY_TIMEOUT = 120.0  # 秒

async def call_gateway_stream(
    messages: list[dict],
    model: str,
    request_id: str,
) -> AsyncGenerator[str, None]:
    """调用网关流式接口"""
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{GATEWAY_BASE_URL}/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "request_id": request_id,
            },
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield line[6:]
```

## 7. 媒体文件过期清理

### Decision: Celery 定时任务 + MinIO 生命周期

**选择**: 结合 Celery Beat 定时任务和 MinIO 生命周期策略

**Rationale**:
- Celery Beat 已用于其他定时任务，复用基础设施
- MinIO 生命周期策略提供兜底清理
- 数据库软删除记录，保留审计日志

**Alternatives Considered**:
- 仅 MinIO 生命周期: 数据库记录无法同步清理
- 手动清理: 容易遗漏，运维负担重

**Implementation Notes**:
```python
# Celery 定时任务
@app.task
def cleanup_expired_media():
    """每日清理过期媒体文件"""
    expired_date = timezone.now() - timedelta(days=7)

    # 1. 查询过期文件
    expired_files = MediaAttachment.objects.filter(
        expires_at__lt=timezone.now(),
        is_expired=False,
    )

    # 2. 从 MinIO 删除原始文件
    for file in expired_files:
        minio_client.remove_object(BUCKET_MEDIA, file.storage_path)

    # 3. 更新数据库状态
    expired_files.update(is_expired=True)

# Celery Beat 配置
CELERYBEAT_SCHEDULE = {
    'cleanup-expired-media': {
        'task': 'apps.chat.tasks.cleanup_expired_media',
        'schedule': crontab(hour=3, minute=0),  # 每日凌晨 3 点
    },
}
```

## 8. 前端媒体上传方案

### Decision: XMLHttpRequest + 进度事件

**选择**: 使用 XMLHttpRequest 实现上传进度监控

**Rationale**:
- fetch API 不支持上传进度事件
- XMLHttpRequest 提供 upload.onprogress 事件
- 可封装为 Promise，保持代码风格一致

**Alternatives Considered**:
- fetch + 无进度: 用户体验差
- 第三方库 (axios): 增加依赖
- tus 分块上传: 过度设计

**Implementation Notes**:
```typescript
// 带进度的上传函数
export async function uploadMedia(
  file: File,
  onProgress: (percent: number) => void,
): Promise<MediaUploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status === 200) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(xhr.responseText));
      }
    };

    xhr.onerror = () => reject(new Error('Upload failed'));

    xhr.open('POST', '/linchat/api/v1/chat/media/upload/');
    xhr.withCredentials = true;  // 携带 Cookie
    xhr.send(formData);
  });
}
```

## 9. 音频录制方案

### Decision: MediaRecorder API + WAV/WebM 格式

**选择**: 使用浏览器原生 MediaRecorder API 录制音频

**Rationale**:
- 浏览器原生 API，无需第三方依赖
- 支持所有现代浏览器
- 可选 WAV（高质量）或 WebM（高压缩）格式

**Alternatives Considered**:
- 第三方库 (RecordRTC): 功能更多但增加依赖
- Web Audio API 手动编码: 复杂度高

**Implementation Notes**:
```typescript
// 音频录制 Hook
export function useAudioRecorder() {
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);

  const startRecording = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mediaRecorder = new MediaRecorder(stream, {
      mimeType: 'audio/webm;codecs=opus',
    });
    const chunks: Blob[] = [];

    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
    mediaRecorder.onstop = () => {
      setAudioBlob(new Blob(chunks, { type: 'audio/webm' }));
      stream.getTracks().forEach((track) => track.stop());
    };

    mediaRecorder.start();
    mediaRecorderRef.current = mediaRecorder;
    setIsRecording(true);
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
  };

  return { isRecording, audioBlob, startRecording, stopRecording };
}
```

## 10. Langfuse 追踪集成

### Decision: 复用现有 Langfuse 集成

**选择**: 扩展现有 Langfuse 追踪机制支持多模态推理

**Rationale**:
- 项目已集成 Langfuse，无需额外配置
- 可追踪多模态推理的延迟、token 消耗、错误率
- 支持按模型、媒体类型分类分析

**Implementation Notes**:
```python
# 多模态推理追踪
from langfuse import Langfuse

langfuse = Langfuse()

async def trace_multimodal_inference(
    user_id: int,
    request_id: str,
    model: str,
    media_types: list[str],
):
    trace = langfuse.trace(
        name="multimodal_inference",
        id=request_id,
        user_id=str(user_id),
        metadata={
            "model": model,
            "media_types": media_types,
        },
    )
    return trace
```
