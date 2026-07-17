# Batch batch-13 执行计划

> 生成时间：2026-07-17
> 类型：refactor（死文件删除） | 优先级：P2 | 风险：low
> 预估：4 文件 / -690 行 / 1 session
> 依赖：无（depends_on 为空）
> SLO 影响：无
> main HEAD：580294a

## 1. 任务理解（一句话）

删除两个死文件（前端 ContextMonitorPanel.design.tsx 静态设计稿、后端 models/tests.py 空桩），并清理 voice 两个 consumer 文件中已废弃的 diarize 注释代码，纯删除、零逻辑改动。

## 2. 涉及文件清单与改动预测

| # | 文件 | 当前行数 | 预计改动行数 | 改动类型 | 风险 | 精简潜力 |
|---|------|---------|------------|---------|------|---------|
| 1 | frontend/src/components/chat/ContextMonitorPanel.design.tsx | 680 | -680 | 整文件删除 | 低 | 高（零 import 引用） |
| 2 | backend/apps/models/tests.py | 3 | -3 | 整文件删除 | 低 | 高（空桩） |
| 3 | backend/apps/voice/consumers.py | — | -2 | 删注释 | 低 | 低 |
| 4 | backend/apps/voice/consumer_session.py | — | -3 | 删注释 | 低 | 低 |

## 3. 核实结论（只读复核，2026-07-17）

- **ContextMonitorPanel.design.tsx（680 行）确认死文件**：全仓 `rg` 唯一提及在 `frontend/src/components/chat/CLAUDE.md:19` 文档表格中，**无任何 .tsx/.ts 代码 import**。真实组件是 `ContextMonitorPanel.tsx`（23501 字节，Mar 20 更新），`page.tsx:26` 引用的是 `@/components/chat/ContextMonitorPanel`（不带 `.design`）。删除设计稿不影响 build。
- **models/tests.py 确认空桩**：仅 3 行 `from django.test import TestCase` + 注释，无测试用例；apps/models 下无其它测试文件；全仓无 `import models.tests` 引用，删除对 pytest 收集无影响。
- **DEPRECATED 注释行号已漂移**（batch-06/07 改过 voice 文件，按内容定位）：
  - `consumers.py`：实际在 **第 65-66 行**（计划书写的 63-64），两行均为纯注释：
    ```
    65: # [DEPRECATED] diarize 功能暂时废弃
    66: # self._diarize_enabled: bool = False
    ```
  - `consumer_session.py`：实际在 **第 78-80 行**（计划书写的 77-79），三行均为纯注释：
    ```
    78: # [DEPRECATED] diarize 功能暂时废弃
    79: # from apps.voice.repositories import speaker_profile_repo
    80: # self._diarize_enabled = await speaker_profile_repo.any_exists()
    ```
  两处均为注释，无活跃代码引用 `_diarize_enabled`，删除后无副作用。

## 4. 详细改动计划

### 文件 1: ContextMonitorPanel.design.tsx
- 操作：`git rm frontend/src/components/chat/ContextMonitorPanel.design.tsx`（整文件删除）。
- 附带：同步删除 `frontend/src/components/chat/CLAUDE.md:19` 该文件的文档行（可选，见第 7 节确认项）。

### 文件 2: models/tests.py
- 操作：`git rm backend/apps/models/tests.py`（整文件删除）。

### 文件 3: consumers.py（第 65-66 行）
- 删除这 2 行注释，保留上下第 64 行和第 67 行不变。

### 文件 4: consumer_session.py（第 78-80 行）
- 删除这 3 行注释，保留第 77 行和第 81 行不变。

## 5. 验证计划

### 5.1 自动化验证（后端局部）
- [ ] `pytest backend/apps/voice/ -v`（voice app 全量，覆盖两个 consumer 改动）
- [ ] `pytest backend/apps/models/ -v`（确认删空桩后收集正常）
- [ ] `ruff check backend/apps/voice/`

### 5.2 前端验证（引用扫描，不实际跑 build）
- [ ] `rg -n "ContextMonitorPanel.design" frontend/src/` → 应仅剩 CLAUDE.md（或删后为空）
- [ ] `rg -n "\.design" frontend/src/**/*.tsx frontend/src/**/*.ts` → 确认无代码引用
- [ ] 是否实际执行 `npm run build` 由 loop 主脑决定（引用扫描已证明零引用，build 不受影响）

### 5.3 回归验证
- [ ] `pytest backend/apps/chat/ backend/apps/graph/ -v`（若 loop 主脑认为需要跨 app 冒烟）

## 6. 回滚策略

`git revert <commit>`（单 commit，纯删除，revert 即完全恢复）。

## 7. 需要安琳确认的事项

- [ ] `frontend/src/components/chat/CLAUDE.md:19` 有该设计稿的文档表格行，`frontend/src/hooks/CLAUDE.md` 亦提及 ContextMonitorPanel。建议随删除同步清理 CLAUDE.md:19 那一行文档（不影响 build，属文档一致性）。是否一并处理？
- 除此之外：✅ 无阻塞事项，核实全部通过，可进入 executor 阶段。

## 8. 执行预算

- tool calls：约 8-12（4 处删除 + 验证）
- 完成时间：单 session 内充裕。
