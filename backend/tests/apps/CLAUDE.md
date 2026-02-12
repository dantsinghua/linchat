# tests/apps 测试指南

> 按 apps 模块组织的测试目录。

---

## 目录结构

```
tests/apps/
├── __init__.py
├── common/
│   └── test_gateway_utils.py    # Gateway 工具测试
├── graph/
│   ├── test_subagents.py        # SubAgent 架构测试
│   ├── test_subagent_autonomy.py # SubAgent 自主性测试
│   ├── test_ha_subagent.py      # HA SubAgent 测试
│   ├── test_ha_client.py        # HAClient API 测试
│   └── test_ha_tools.py         # HA 工具集测试
└── models/
    └── __init__.py
```

---

## 运行命令

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

pytest tests/apps/ -v
pytest tests/apps/graph/ -v
pytest tests/apps/common/ -v
```
