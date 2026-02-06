# Quickstart: Home Assistant SubAgent 开发指南

## 前置条件

1. Home Assistant 实例可访问（内网 IP 或 frp 穿透）
2. 在 HA 中创建 Long-Lived Access Token:
   - HA 面板 → 用户头像 → Security → Long-Lived Access Tokens → Create Token
3. LinChat 虚拟环境已激活

## 配置

在 `backend/.env` 中添加:

```bash
HA_URL=http://192.168.1.100:8123    # HA 实例地址
HA_TOKEN=your_long_lived_token       # HA Access Token
# 可选
HA_REQUEST_TIMEOUT=10                # HTTP 超时（默认 10 秒）
HA_BLOCKED_ENTITIES=lock.front_door,cover.garage  # 黑名单设备（逗号分隔）
```

## 验证 HA 连接

```bash
# 测试 HA API 可达性
curl -s -H "Authorization: Bearer $HA_TOKEN" http://192.168.1.100:8123/api/ | python -m json.tool

# 期望输出: {"message": "API running."}
```

## 开发流程

### 1. 添加/修改 HA 工具

编辑 `backend/apps/graph/tools/homeassistant.py`:

```python
from langchain_core.tools import tool

@tool
async def ha_query(query_type: str, entity_id: str | None = None, ...) -> str:
    """查询 Home Assistant 设备状态。"""
    # 实现查询逻辑
    ...

HA_TOOLS = [ha_query, ha_control, ha_diagnose]
```

### 2. 修改 SubAgent Prompt

编辑 `backend/apps/graph/subagents/ha_agent.py` 中的 `HA_PROMPT`。

### 3. 运行测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行 HA 相关测试
pytest tests/apps/graph/test_ha_client.py -v
pytest tests/apps/graph/test_ha_tools.py -v
pytest tests/apps/graph/test_ha_subagent.py -v

# 运行所有测试确认无回归
pytest tests/ -v
```

### 4. 手动验证

```bash
# 重启后端
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

在 LinChat 中测试:
- "帮我开客厅灯"
- "客厅温度多少"
- "检查智能家居系统"

## 新增 HA 设备类型

在 `homeassistant.py` 的 `ACTION_MAP` 字典中添加映射:

```python
ACTION_MAP = {
    "turn_on": ("homeassistant", "turn_on", {}),
    "new_action": ("domain", "service", {"required_param": "default"}),
}
```

## 文件结构

```
backend/apps/graph/
├── subagents/
│   ├── __init__.py        # 条件注册: if HA_ENABLED → ha_subagent
│   └── ha_agent.py        # ha_subagent + HA_PROMPT
└── tools/
    ├── ha_client.py       # HAClient HTTP 封装
    └── homeassistant.py   # ha_query + ha_control + ha_diagnose + HA_TOOLS
```
