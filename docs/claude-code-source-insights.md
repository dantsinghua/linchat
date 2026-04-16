# Claude Code Source Insights for LinChat

Source: `/home/dantsinghua/git/claude-code/typescript-source/src` (1,884 files, 44 tools, 22 service subsystems)

---

## High Priority — Directly Applicable

### 1. Coordinator Multi-Agent Orchestration

**Source**: `src/coordinator/coordinatorMode.ts`

Current linchat SubAgents run serially (main agent -> document_subagent -> wait). Claude Code uses a **coordinator pattern**:

- One coordinator dispatches multiple workers, workers run **async in parallel**
- Workers report results via notifications, coordinator doesn't block
- Each worker has isolated context

**LinChat action**: When user asks "parse this PDF and check dehumidifier status", document_subagent and ha_subagent can run in parallel via Celery chord, results collected and unified before reply.

### 2. Streaming Tool Execution + Concurrency Safety

**Source**: `src/services/tools/StreamingToolExecutor.ts`

```
Read-only tools  -> concurrent (doc_search x3 simultaneously)
Write tools      -> serial (document_parse holds GPU lock exclusively)
```

Claude Code marks tools with `isConcurrencySafe()`. LinChat currently runs all tools serially. Can make `doc_search`, `doc_list`, `mem_search` concurrent; only `document_parse` (GPU lock) stays serial.

### 3. Session Memory Extraction

**Source**: `src/services/SessionMemory/sessionMemory.ts`

- Background monitoring of token growth, triggers at thresholds
- Uses forked subagent to extract key info (doesn't interrupt main conversation)
- Stores as structured markdown, injected into next conversation

**LinChat action**: Celery periodic task extracts from long conversations: user preferences, document summaries, HA automation rules. Store in `user_memory` table, auto-inject into system prompt.

### 4. Context Compaction Strategies

**Source**: `src/services/compact/`

5 compaction modes:
- **Auto Compact**: triggered when token budget exceeded
- **Micro Compact**: lightweight, non-blocking
- **Snip Compact**: removes intermediate reasoning, keeps conclusions only

LinChat's context monitor already tracks token usage (5.4%), but lacks active compaction. Trigger when pct > 60%.

---

## Medium Priority — Architecture Improvement

### 5. Hook Extension System

**Source**: `src/utils/hooks.ts` (1000+ lines)

Full lifecycle hooks:
```
PreToolUse      -> intercept before execution (e.g. check file size before parse)
PostToolUse     -> post-processing (e.g. auto-index after parse)
SubagentStart/Stop -> agent lifecycle
FileChanged     -> file change notification
```

**LinChat action**: Django signal + Celery. e.g. `post_document_parse` hook auto-triggers embedding indexing without hardcoding in parse logic.

### 6. Permission Modes

**Source**: `src/utils/permissions/PermissionMode.ts`

```
yolo  -> auto-allow (document_subagent reading docs)
plan  -> plan first, confirm before execution (ha_subagent controlling devices)
deny  -> forbidden (search_subagent cannot write)
```

LinChat subagents currently have no permission tiers. ha_subagent controlling lights and document_subagent reading docs should have different permission levels.

### 7. Mailbox Inter-Agent Communication

**Source**: `src/context/mailbox.tsx`, `src/utils/mailbox.ts`

Agents communicate via mailbox:
```
coordinator -> send("document_subagent", "parsing done?")
document_subagent -> reply("done, 61K chars")
```

**LinChat action**: Redis pub/sub, each subagent subscribes to its channel. More flexible than Celery result, supports intermediate status notifications.

### 8. Typed Tool Progress

**Source**: `src/types/tools.ts`

Each tool has a dedicated progress type:
```
BashProgress   -> PID, stdout/stderr chunks
AgentProgress  -> sub-agent state
MCPProgress    -> MCP server events
```

**LinChat action**:
- `DocParseProgress` -> current page / total pages, ETA
- `TTSProgress` -> synthesis percentage
- `HAProgress` -> device response status

Push to frontend via WebSocket for real-time progress bars.

---

## Low Priority — Long-term Planning

### 9. Token Budget Management

**Source**: `src/query/tokenBudget.ts`

Per-session / per-user budget cap, auto-compact or stop when exceeded. LinChat uses multiple LLMs (kimi-k2.5, local models), cost control matters.

### 10. Feature Gate Experimentation

**Source**: `src/services/analytics/growthbook.ts`

Feature flags for gradual rollout:
- Canary new subagent types
- A/B test different system prompts
- Per-user toggle for memory extraction

### 11. Virtual Message List

**Source**: `src/components/VirtualMessageList.tsx`

Virtualized rendering for 1000+ messages. LinChat frontend needs this for long conversations.

---

## Key Source Files to Study

| File | Lines | Value | Priority |
|------|-------|-------|----------|
| `src/coordinator/coordinatorMode.ts` | 370 | Orchestration pattern | CRITICAL |
| `src/tools/AgentTool/AgentTool.tsx` | 800+ | Agent spawning, progress | CRITICAL |
| `src/query.ts` | 1,729 | Message loop, streaming | HIGH |
| `src/QueryEngine.ts` | 1,295 | Context assembly | HIGH |
| `src/Tool.ts` | 792 | Tool interface | HIGH |
| `src/services/tools/StreamingToolExecutor.ts` | 300+ | Concurrency control | HIGH |
| `src/utils/hooks.ts` | 1,000+ | Hooks system | HIGH |
| `src/state/AppState.tsx` | 150+ | State management | MEDIUM |
| `src/utils/swarm/teamHelpers.ts` | 300+ | Team coordination | MEDIUM |
| `src/services/SessionMemory/sessionMemory.ts` | 300+ | Memory extraction | MEDIUM |
| `src/utils/permissions/permissions.ts` | 400+ | Permission logic | MEDIUM |
| `src/services/compact/compact.ts` | 400+ | Compaction strategy | MEDIUM |

---

## Implementation Roadmap

| Phase | Content | LinChat Components |
|-------|---------|-------------------|
| **P1** | SubAgent parallel execution + streaming progress | backend agent, WebSocket, frontend |
| **P1** | incomplete polling fix (done) | document_parse_helpers.py |
| **P2** | Session memory auto-extraction | Celery task, user_memory table |
| **P2** | Context compaction | context_monitor, LangGraph |
| **P2** | Hook system | Django signals |
| **P3** | Permission tiers | subagent config, Django auth |
| **P3** | Token budgeting | settings, middleware |
| **P4** | Feature gate | Redis-based flags |
