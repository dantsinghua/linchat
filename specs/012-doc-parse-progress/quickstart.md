# Quickstart: 012-doc-parse-progress

**Date**: 2026-03-06

## 快速验证步骤

### 1. 后端验证

```bash
# 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行全量测试
pytest

# 运行文档解析相关测试
pytest tests/apps/graph/test_document_agent.py -v
pytest tests/chat/test_document_parse_service.py -v
```

### 2. 前端验证

```bash
cd /home/dantsinghua/work/linchat/frontend

# 编译检查
npm run build
```

### 3. E2E 验证

1. 启动后端: `uvicorn core.asgi:application --host 0.0.0.0 --port 8002`
2. 启动前端: `npm run build && npm run start -- -p 3784`
3. 使用 `/linchat-login` 自动登录
4. 上传一份多页 PDF（如 `2509.04664v1.pdf`，36 页）
5. 观察聊天区域底部：
   - 应出现 pending → processing（逐页递增）→ completed → 自动消失
   - AI 应输出解析结果

### 4. frpc 容错验证

```bash
# 在解析过程中模拟 frpc 短暂中断
sudo kill -STOP $(pgrep frpc)
sleep 3
sudo kill -CONT $(pgrep frpc)

# 预期：轮询自动重试，进度条不出现错误
```

### 5. incomplete 状态验证

当 Gateway 引擎崩溃导致部分完成时：
- 进度条应显示橙色 "部分完成" 警告
- AI 应输出已成功解析的页面内容
- 内容末尾附 "⚠️ 部分页面解析失败" 提示

## 关键文件

| 文件 | 改动要点 |
|------|---------|
| `backend/apps/graph/subagents/document_agent.py` | 轮询循环 SSE 推送 + incomplete 处理 |
| `backend/apps/media/services/document.py` | poll_task_status 网络重试 |
| `frontend/src/stores/chatStore.ts` | docParseProgress 全局状态 |
| `frontend/src/hooks/useAuth.tsx` | SSE 事件写入 chatStore |
| `frontend/src/components/chat/MessageList.tsx` | DocParseProgressBar 组件 |
