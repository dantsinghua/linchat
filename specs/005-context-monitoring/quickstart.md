# Quickstart: M1c 动态监控

**Feature**: 005-context-monitoring
**Branch**: `005-context-monitoring`

## 开发环境准备

```bash
# 切换到特性分支
git checkout 005-context-monitoring

# 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 确保 Docker 服务运行（Redis 是必需的）
cd /home/dantsinghua/work/linchat
docker compose ps
```

## 后端开发

### 文件修改顺序

1. `backend/apps/context/types.py` — 新增 TokenBreakdown
2. `backend/apps/context/builder.py` — 新增 build_preamble_with_breakdown()
3. `backend/apps/context/monitoring.py` — 新建 ContextMonitor + AlertLevel
4. `backend/apps/common/event_service.py` — 新增 publish_event()
5. `backend/apps/graph/services/agent_service.py` — 改造 _build_prompt_preamble() + execute() 埋点
6. `backend/apps/graph/tools/memory.py` + `context.py` + `search.py` — 调用 cap_tool_result()
7. `backend/apps/memory/tasks.py` — 新增 embedding_health_check
8. `backend/core/settings.py` — LOGGING 配置 + 常量
9. `backend/core/celery.py` — beat_schedule 新增

### 运行后端

```bash
cd /home/dantsinghua/work/linchat/backend
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

### 运行测试

```bash
cd /home/dantsinghua/work/linchat/backend
pytest tests/context/test_monitoring.py -v
```

## 前端开发

### 文件修改顺序

1. `frontend/src/types/index.ts` — 新增 MonitorData / MemoryRecord / ToolProcess / TokenBreakdown / AlertLevel / ContextStatus 类型
2. `frontend/src/hooks/useAuth.tsx` — handleSSEEvent 扩展 context_status 事件分发
3. `frontend/src/components/chat/ContextStatusBar.tsx` — 新建状态提示条组件
4. `frontend/src/components/chat/ContextMonitorPanel.tsx` — 新建监控侧边栏（参照 design.tsx）
5. `frontend/src/app/chat/page.tsx` — 集成 MonitorSidebar + MonitorToggleButton + ContextStatusBar

### 构建与运行

```bash
cd /home/dantsinghua/work/linchat/frontend
npm run build
npm run start -- -p 3784
```

## 验证步骤

1. 启动后端 + 前端
2. 登录 LinChat，进入聊天页面
3. 点击右上角"监控"按钮展开侧边栏，确认四个区块渲染正常（大模型输入输出/当前上下文/当前记忆/当前进程）
4. 发送消息，观察大模型输入输出区块折线图是否实时更新输入/输出 token 趋势
5. 观察当前上下文区块堆叠柱状图是否展示上下文各部分占比
6. 观察当前记忆区块是否展示语义标签 token 占比和前 4 条记忆记录
7. 触发工具调用（如搜索），观察当前进程区块是否出现工具调用记录
8. 持续对话直到 token 使用率超过 70%，观察输入框下方是否出现蓝色状态条 + "超过70%将会自动压缩会话"
9. 检查后端日志中是否输出 `apps.context.monitoring` 的结构化日志
10. 收起侧边栏，确认聊天区域恢复全宽

## 关键配置

| 配置项 | 位置 | 默认值 |
|--------|------|--------|
| WARNING 阈值 | `apps/context/monitoring.py` | 0.70 (70%) |
| CRITICAL 阈值 | `apps/context/monitoring.py` | 0.90 (90%) |
| 工具截断上限 | `core/settings.py` | 1500 tokens |
| 健康检查间隔 | `core/celery.py` | 每小时 (crontab minute=0) |
