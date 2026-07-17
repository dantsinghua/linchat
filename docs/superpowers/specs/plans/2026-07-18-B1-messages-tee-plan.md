# B1 执行计划 — wechat-narrator 对话旁路落库

> 由 init-b1 产出、主 agent 自审 + 裁决。executor 严格照此实施。**零 LinChat 触碰**。

## 主 agent 裁决（覆盖 init-b1 待决项）
1. **测试框架 = stdlib unittest**（不装 pytest，零新依赖）。`python3 -m unittest`。
2. **synth 摄入 = 可插拔 hook**：`msg_synth` 尝试 `import linchat_ingest_client`（B2 产出）；**缺失时降级为写本地 `~/.wechat-narrator/synth/YYYY-MM-DD.md` 且不 mark_ingested**（待 B2 接线后重跑摄入）。B1 独立可测。
3. **content_hash = 接受同日同人同文本去重合一**，docstring 显式标注此权衡。
4. **backfill 本期不做**（延后可选，不写 backfill_messages.py）。B1 交付 = messages_store + tee + msg_synth + timer + unittest。
5. **executor 边界**：只改文件 + `py_compile`/import 静态检查 + unittest；**不 kill 进程/不重启服务/不 git**。改运行中的 `wechat_group_to_email.py` 后，重拉服务与 git 提交交安琳手动（给命令）。

## 环境（executor 必读）
- Python：系统 `/usr/bin/python3` 3.10.12（**非** LinChat venv）；sqlite3 3.37.2
- 目录：`/home/dantsinghua/clawd/scripts/wechat-narrator/`
- **绝不** restart `wechat-narrator.service`（清登录态强制重扫码）
- LLM 网关 `_openclaw()` @ `wechat_auto_reply.py:134`，常离线 → synth 必须降级

## 交付物
| 动作 | 文件 |
|---|---|
| 新建 | `messages_store.py`、`msg_synth.py`、`tests/test_messages_store.py` |
| 新建 | `~/.config/systemd/user/wn-msg-synth.{timer,service}`（写文件，**不 enable**，交安琳） |
| 改 | `wechat_group_to_email.py`：顶部 import + 三注入点 tee（全 try/except） |

## 1. messages_store.py（新建）

### schema
```sql
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session TEXT NOT NULL, kind TEXT NOT NULL,
  sender TEXT, msg_type TEXT DEFAULT 'text',
  text TEXT, assets TEXT,
  ts INTEGER, day_bucket TEXT,
  content_hash TEXT UNIQUE NOT NULL,
  ingested INTEGER DEFAULT 0, created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_msg_ingested ON messages(ingested, day_bucket);
CREATE INDEX IF NOT EXISTS idx_msg_session  ON messages(session, ts);
```

### 连接/PRAGMA + 幂等哈希（day_bucket 替代 ts，避免重跑破坏幂等）
```python
import hashlib, json, os, sqlite3, time

DB_PATH = os.environ.get("WN_MESSAGES_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "messages.db"))

def _connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA synchronous=NORMAL"); return db

def _ensure_schema(db):
    db.executescript("""<上面的 CREATE TABLE + INDEX>""")

def _hash(session, kind, sender, msg_type, text, day):
    raw = f"{session}\x1f{kind}\x1f{sender}\x1f{msg_type}\x1f{text}\x1f{day}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

### tee() — 绝不 raise
```python
def tee(session, kind, sender, text, msg_type="text", assets=None, ts=None):
    """旁路落库；任何异常只 log 不抛。返回 True=新入库/False=去重或失败。
    幂等权衡：content_hash 用 day_bucket 而非精确 ts，故同日同人同文本会去重合一
    （对日合成要点用途可接受）。"""
    try:
        ts = int(ts if ts is not None else time.time())
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        text = text or ""; sender = sender or "unknown"
        h = _hash(session, kind, sender, msg_type, text, day)
        aj = json.dumps(assets, ensure_ascii=False) if assets else None
        db = _connect()
        try:
            _ensure_schema(db)
            cur = db.execute("INSERT OR IGNORE INTO messages"
              "(session,kind,sender,msg_type,text,assets,ts,day_bucket,content_hash,ingested,created_at)"
              " VALUES(?,?,?,?,?,?,?,?,?,0,?)",
              (session,kind,sender,msg_type,text,aj,ts,day,h,int(time.time())))
            db.commit(); return cur.rowcount > 0
        finally: db.close()
    except Exception as e:
        try:
            from wn_logging import get_logger
            get_logger("messages_store").warning(f"tee failed: {e}")
        except Exception: pass
        return False
```

### fetch_pending(day=None, limit=5000) + mark_ingested(ids)
- `fetch_pending`：`SELECT id,session,kind,sender,msg_type,text,assets,ts,day_bucket FROM messages WHERE ingested=0 [AND day_bucket=?] ORDER BY ts LIMIT ?` → dict list
- `mark_ingested`：`UPDATE messages SET ingested=1 WHERE id IN (...)`；幂等，异常只 log

## 2. tee 注入点（改 wechat_group_to_email.py，全 try/except）
顶部仿 `:43-52` 风格 `try: import messages_store` 一次。

**A · 群单条（`:361` `send_mail(cfg, who, text, kind, attachments)` 之后）**
```python
try:
    messages_store.tee(who, kind, "group", text, assets=attachments)
except Exception: pass
```
**B · 群洪峰（`:344` send_mail 之后、`:346` continue 之前）** —— 用结构化 msgs 逐条
```python
try:
    for m in msgs:
        messages_store.tee(who, kind, m.get("sender") or "unknown",
                           m.get("text") or "", msg_type=m.get("kind","text"))
except Exception: pass
```
**C · 私聊（`:309` send_mail 之后、`:310` continue 之前）** —— 逐条 + AI 回复
```python
try:
    for m in r["new_msgs"]:
        messages_store.tee(who, kind, m.get("sender") or "unknown",
                           m.get("text") or "", msg_type=m.get("kind","text"))
    if r.get("reply"):
        messages_store.tee(who, kind, "assistant", r["reply"])
except Exception: pass
```
⚠ 私聊早退降级分支（`:268/275/280/284`）**不 tee**（无结构化 sender）。改前先读文件核对真实行号/变量名。

## 3. msg_synth.py（新建）
逻辑：`--day`（默认昨日）→ `fetch_pending(day)` → 空退出0 → 按 session 分组拼对话（assistant 标「AI老公」）→ 调 `_openclaw`（从 `wechat_auto_reply` import，model=CHAT_MODEL）合成要点（≤10000 字）→ **可插拔摄入 hook**（尝试 import linchat_ingest_client；缺失→写 `~/.wechat-narrator/synth/{day}.md` 且**不 mark_ingested**）→ 摄入成功才 `mark_ingested(ids)` → 全程 try/except。网关离线→log 降级、不 mark、退出非0。

## 4. systemd user timer（写文件，不 enable）
`~/.config/systemd/user/wn-msg-synth.timer`：`OnCalendar=*-*-* 04:30:00` + `Persistent=true`（错峰 greenmail 04:10，合成"昨日"不切当天）
`wn-msg-synth.service`：`Type=oneshot` + `WorkingDirectory` + `ExecStart=/usr/bin/python3 .../msg_synth.py`
（executor 只写文件；`daemon-reload`+`enable --now` 交安琳）

## 5. 测试 tests/test_messages_store.py（unittest，临时 db）
setUp 设 `os.environ["WN_MESSAGES_DB"]=tempfile`；tearDown 清理。**绝不碰生产 messages.db**。
用例：tee_inserts / tee_idempotent_same_day（同条两次仅1行，第2次 False）/ tee_distinct_across_days / **tee_never_raises**（不可写 db→False 不抛）/ private_routing（self/peer/assistant 三角色）/ group_flood_routing（sender 缺失→unknown）/ mark_ingested / fetch_pending_by_day / **synth_degrades_on_gateway_down**（mock _openclaw 抛异常→不 mark、不崩、退出非0）

## 6. 验证（executor 必做）
1. 静态：`cd .../wechat-narrator && python3 -c "import messages_store"` + `python3 -m py_compile wechat_group_to_email.py msg_synth.py`
2. 单测：`python3 -m unittest tests.test_messages_store -v` 全绿
3. **不** kill 进程/不重启/不 git —— 报告 + 给安琳后续命令

## 7. 交安琳的后续命令（executor 在报告里给出，不自己执行）
- 重拉 group2email 生效：`pkill -f wechat_group_to_email`（supervisor 自动重拉；**勿** restart wechat-narrator.service）
- 启用 timer：`systemctl --user daemon-reload && systemctl --user enable --now wn-msg-synth.timer`
- git 提交（clawd 仓库）：`cd /home/dantsinghua/clawd && git add scripts/wechat-narrator/{messages_store.py,msg_synth.py,tests,wechat_group_to_email.py} && git commit -m "feat(wechat-narrator): 对话旁路落库 + 日合成（B1）"`
- 活体验证：真机发消息 → `sqlite3 messages.db "SELECT session,kind,sender,substr(text,1,20),ingested FROM messages ORDER BY id DESC LIMIT 5"`；降级验证 `chmod 000 messages.db` → 邮件照常到 GreenMail、log 仅 warning

## 红线合规
零 LinChat 触碰；不引入新依赖（unittest）；tee 全降级不影响转发主流程；不裸操作生产库（临时 db 测试）；运维/git 交安琳。
