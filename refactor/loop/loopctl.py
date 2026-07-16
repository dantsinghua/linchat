#!/usr/bin/env python3
"""Unattended refactor-loop controller. Subcommands:
  next                    -> print next runnable batch id (deps+priority aware), or ALL_DONE
  mark <id> <status>      -> set actual_status in 04-refactor-plan.json + append to progress.txt
  gate <id>               -> auto-review plan.md against forbidden zones; exit 0=PASS
  summary                 -> one-line per batch status table
  alert <sev> <subj> <body>  -> email via GreenMail (sev: remind|urgent), X-WN-Category: refactor
State: refactor/loop/state.json (consecutive_failures, current_batch, started_at)."""
import json, os, re, smtplib, subprocess, sys, time
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLAN = os.path.join(ROOT, "refactor/04-refactor-plan.json")
BATCH_DIR = os.path.join(ROOT, "refactor/batches")
STATE = os.path.join(ROOT, "refactor/loop/state.json")
PRIORITY = ["P0-Day1", "P0", "P0-followup", "P1", "P2", "P3"]
# forbidden path patterns derived from plan global_constraints (backend-only scope)
FORBIDDEN_PATHS = [r"backend/.*/migrations/", r"docker-compose\.yml", r"frontend/(?!src/)"]
MAX_LINES_CHANGED = 800  # gate cap: bigger plans need a human


def load_plan():
    return json.load(open(PLAN))


def progress_status(bid):
    """Latest STATUS from progress.txt; batches may share a file (e.g. 01b in batch-01)."""
    for f in (f"{bid}-progress.txt", f"{bid.rsplit('b', 1)[0]}-progress.txt"):
        p = os.path.join(BATCH_DIR, f)
        if os.path.exists(p):
            m = re.findall(r"^STATUS:\s*(\S+)", open(p).read(), re.M)
            if m:
                return m[-1]
    return None


def effective_status(b):
    st = (b.get("actual_status") or "planned").lower()
    ps = (progress_status(b["id"]) or "").lower()
    return "completed" if "completed" in (st, ps) or "completed" in ps else st


def cmd_next(plan):
    done = {b["id"] for b in plan["batches"] if effective_status(b) == "completed"}
    todo = [b for b in plan["batches"]
            if effective_status(b) in ("planned", "failed_retry") and b["id"] not in done]
    todo.sort(key=lambda b: (PRIORITY.index(b.get("priority", "P3"))
                             if b.get("priority") in PRIORITY else 9, b["id"]))
    for b in todo:
        if all(d in done for d in b.get("depends_on", [])):
            print(b["id"])
            return
    print("ALL_DONE" if not todo else "BLOCKED:" + ",".join(b["id"] for b in todo[:3]))


def cmd_mark(plan, bid, status):
    for b in plan["batches"]:
        if b["id"] == bid:
            b["actual_status"] = status
            json.dump(plan, open(PLAN, "w"), ensure_ascii=False, indent=2)
            pf = os.path.join(BATCH_DIR, f"{bid}-progress.txt")
            with open(pf, "a") as f:
                f.write(f"\nSTATUS: {status.upper()}  # loopctl {time.strftime('%F %T')}\n")
            print(f"marked {bid} -> {status}")
            return
    sys.exit(f"unknown batch {bid}")


def cmd_gate(plan, bid):
    b = next((x for x in plan["batches"] if x["id"] == bid), None) or sys.exit(f"no {bid}")
    plan_md = os.path.join(BATCH_DIR, f"{bid}-plan.md")
    errs = []
    if not os.path.exists(plan_md):
        errs.append("plan.md missing")
    else:
        text = open(plan_md).read()
        for pat in FORBIDDEN_PATHS:
            for line in re.findall(r"^.*(?:files?_touched|改动文件|- `).*$", text, re.M):
                if re.search(pat, line):
                    errs.append(f"forbidden path in plan: {line.strip()[:80]}")
    if b.get("scope", {}).get("forbidden_zones_crossed"):
        errs.append("plan JSON says forbidden_zones_crossed=true")
    if b.get("estimated_lines_changed", 0) > MAX_LINES_CHANGED:
        errs.append(f"lines_changed {b['estimated_lines_changed']} > cap {MAX_LINES_CHANGED}")
    print("GATE:" + ("PASS" if not errs else "FAIL " + "; ".join(errs)))
    sys.exit(0 if not errs else 1)


def cmd_summary(plan):
    for b in plan["batches"]:
        print(f"{b['id']:<14} {b.get('priority', ''):<12} {effective_status(b):<12} {b['title'][:50]}")


def cmd_alert(sev, subj, body):
    tag = "紧急" if sev == "urgent" else "提醒"
    m = MIMEText(body, _charset="utf-8")
    m["Subject"] = f"[{tag}][重构] {subj}"
    m["From"] = "wechat-narrator@test.local"
    m["To"] = "wechat-narrator@test.local"
    m["X-WN-Category"] = "refactor"
    m["X-WN-Severity"] = sev
    s = smtplib.SMTP("127.0.0.1", 3025, timeout=10)
    s.send_message(m)
    s.quit()
    print("alert sent")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "alert":
        cmd_alert(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        plan = load_plan()
        {"next": lambda: cmd_next(plan),
         "mark": lambda: cmd_mark(plan, sys.argv[2], sys.argv[3]),
         "gate": lambda: cmd_gate(plan, sys.argv[2]),
         "summary": lambda: cmd_summary(plan)}[cmd]()
