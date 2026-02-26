# scripts 模块指南

> 后端工具脚本目录，包含一次性初始化和运维脚本。

---

## 文件列表

| 文件 | 职责 |
|------|------|
| `init_minio.py` | MinIO Bucket 初始化脚本 |

---

## init_minio.py

MinIO 对象存储初始化脚本，创建多模态功能所需的 Bucket 并配置生命周期策略。

### 创建的 Bucket

| Bucket | 用途 | 生命周期 |
|--------|------|----------|
| `linchat-media` | 媒体文件存储（图片/视频/音频/文档） | `media/` 前缀下文件 7 天自动过期 |
| `linchat-thumbnails` | 缩略图存储 | 永久保留 |

### 使用方式

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
python scripts/init_minio.py
```

### 必需环境变量

| 变量 | 说明 |
|------|------|
| `MINIO_ENDPOINT` | MinIO 端点（默认 `localhost:9010`） |
| `MINIO_ACCESS_KEY` | MinIO 访问密钥（必填） |
| `MINIO_SECRET_KEY` | MinIO 密钥（必填） |
| `MINIO_SECURE` | 是否使用 HTTPS（默认 `false`） |
| `MINIO_BUCKET_MEDIA` | 媒体桶名（默认 `linchat-media`） |
| `MINIO_BUCKET_THUMBNAILS` | 缩略图桶名（默认 `linchat-thumbnails`） |

### 依赖

- `minio` Python SDK
- `python-dotenv`（从 `.env` 加载环境变量）

### 注意事项

- 脚本为幂等操作，Bucket 已存在时跳过创建
- 需要在 Docker 的 MinIO 服务已启动后执行
- 首次部署或重建 MinIO 时需执行此脚本
