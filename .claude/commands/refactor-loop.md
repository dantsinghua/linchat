---
description: 无人值守重构循环 — 自动逐 batch 执行 04-refactor-plan.json 直到全绿+性能达标。用法：/refactor-loop [max_batches]
---

# LinChat Unattended Refactor Loop

Runs the 28-batch refactor plan hands-free. One sentence to start; loop picks, plans,
gates, executes, validates, merges — batch after batch — until goals are met or a stop
condition fires. Human review gates from phase2-* commands are REPLACED by scripted
gates + deferred manual-review backlog (unattended policy, user-approved 2026-07-17).

## Goals (fixed by 安琳, 2026-07-17)
1. FULL backend test suite green — zero failed/error (zero-bug policy; gate before EVERY merge).
2. Primary perf metric `voice_e2e_p50_ms` (会话响应总时长) improved ≥20% vs baseline
   `refactor/loop/baseline-metrics.json` (historical 2026-04-16: 10828ms → target ≤8662ms;
   plan SLO stricter: 5000ms — keep going through SLO batches even after 20% is met).

## Tooling (refactor/loop/)
- `loopctl.py next|mark|gate|summary|alert` — batch selection (deps+priority, reconciles
  progress.txt vs plan JSON), status marking, plan auto-review gate, email alerts
  ([紧急/提醒][重构] via GreenMail :3025 → Outlook).
- `validate_full.sh` — full pytest, writes last-validation.json, exit 0 iff all green.
  Self-escapes the 512-pid session cgroup via systemd-run (do NOT wrap it yourself).
- `perf_bench.sh [--baseline]` — voice E2E p50 (from backend log) + api p95; prints
  `PERF_TARGET: MET|NOT_MET` (insufficient primary data ⇒ NOT_MET).
- Subagents (unchanged): `.claude/agents/batch-{initializer,executor,validator}.md`.

## Loop protocol — one batch per iteration
**Preflight (first iteration only):** cd ~/work/linchat; `git checkout main && git pull`;
working tree must be clean (leftovers → `git stash push -m loop-stash`); backend service up
(`scripts/services.sh status`, containers postgres/redis/minio up); baseline-metrics.json exists.
Environment (2026-07-17 crash postmortem — tmux segfault + cgroup 3G/512pid exhaustion +
mid-run CLI auto-update): the loop session SHOULD be launched via `refactor/loop/start-loop.sh`
(pinned binary, DISABLE_AUTOUPDATER=1, own scope 4.5G/1024pids). If `DISABLE_AUTOUPDATER` is
unset in this session, alert remind once and continue — but re-verify before each batch that
`readlink -f $(command -v claude)` still exists (auto-update deletes old binaries; missing ⇒
subagent panes die with status 127).

1. `BID=$(python3 refactor/loop/loopctl.py next)`
   - `ALL_DONE` → enter **Rediagnosis stage** (below). Loop only SUCCEEDS when a diagnosis
     round yields zero new batches AND validate_full.sh green AND perf_bench.sh MET —
     then `loopctl alert remind "重构全部完成" <报告>` + final report; STOP.
   - `BLOCKED:*` → alert urgent + STOP.
2. **Plan** — if `refactor/batches/$BID-plan.md` missing: spawn `batch-initializer` subagent
   (its documented flow; output plan.md + progress `STATUS: PLAN_READY`).
3. **Gate** — `python3 refactor/loop/loopctl.py gate $BID`. FAIL → `loopctl mark $BID blocked_gate`,
   alert remind with reasons, `continue` to next batch. (Replaces human "execute confirmed".)
4. **Execute** — FIRST reconcile any pre-existing `refactor/$BID` branch (April-era batches
   04/05/06 have unmerged branches + worktrees with finished work):
   a. `git merge --no-commit --no-ff refactor/$BID` onto a clean main — clean merge →
      `git merge --abort`, then run validate_full.sh on the BRANCH; green ⇒ skip straight to
      step 6 (reuse the old work, merge it — don't redo).
   b. Conflicts or red validation ⇒ `git merge --abort`; archive first (`git tag archive/$BID
      refactor/$BID`), remove its worktree if any (`git worktree remove ../linchat-$BID
      --force`), delete the branch, then proceed to a fresh execute below.
   Then: `git tag -f before-$BID`; spawn `batch-executor` subagent (branch `refactor/$BID`,
   its 9-step flow, retry cap 3). FAILED → `git checkout main && git reset --hard before-$BID` is
   NOT needed on main (work was on branch); delete branch, `loopctl mark $BID failed`, failures+=1, goto 7.
5. **Validate** — on the batch branch: `refactor/loop/validate_full.sh` MUST pass (all green).
   For P1 `blocks_slo` batches also run perf_bench.sh — primary-metric regression vs baseline → treat
   as FAILED (step 4 failure path). batch-validator's manual checklist items: append verbatim to
   `refactor/loop/manual-review-backlog.md` under `## $BID` — deferred, non-blocking.
6. **Merge** — `git checkout main && git merge --no-ff refactor/$BID -m "merge(refactor): $BID <title>"`;
   run validate_full.sh ONCE more on main (paranoia gate); push; `loopctl mark $BID completed`; failures=0.
7. **Iterate** — log one-line progress (`loopctl summary | tail`), then next iteration. If self-pacing
   across long waits use ScheduleWakeup 60–270s; otherwise continue in-session.

## Rediagnosis stage (covers code added AFTER the 2026-04-16 plan — keeps the loop self-updating)
Runs when `next` says ALL_DONE. Max **3 rounds per loop run** (count `refactor/diag-*/` dirs
created this run); hitting the cap without convergence → urgent alert + STOP (something is churning).

R1. Scope: `LAST=$(git tag -l 'diag-*' | sort | tail -1)`; incremental target =
    `git diff --stat ${LAST:-before-batch-04}..HEAD -- backend/` + backend logs since last round.
R2. Spawn the Phase-1 subagents INCREMENTALLY (same contracts as /phase1-init, read-only):
    `architecture-analyzer` → `refactor/diag-<YYYYMMDD>/01-architecture-delta.md` (changed modules only);
    `log-diagnostician`     → `.../02-issue-diagnosis.md` (runtime errors = zero-bug hunting ground);
    `call-chain-profiler`   → `.../03-hotpath-delta.md` (only if perf target not yet MET).
    Each returns ≤200-char summary to the main loop; do NOT read their full output here.
R3. Spawn `refactor-planner`: input = the delta reports + current 04-refactor-plan.json;
    it APPENDS new batches (ids `batch-29+`, unique, with depends_on/priority/scope like existing
    entries) directly into 04-refactor-plan.json and writes `.../05-addendum.md`. New-code debt
    (e2e harness, loop scripts, anything post-April) is in scope; global_constraints unchanged.
R4. `git add refactor/ && git commit -m "chore(refactor): diagnosis round <date>" && git tag diag-<date> && git push`.
R5. Zero new batches appended → run validate_full.sh + perf_bench.sh for the final verdict (see step 1).
    Otherwise → alert remind "诊断新增 N 个 batch" + goto loop step 1.

## Stop conditions (always send alert + write final report to refactor/loop/loop-report-<date>.md)
- 2 consecutive batch failures → urgent alert, STOP.
- Disk free <2G or MemAvailable <500M → urgent alert, STOP (this 8G/4-core VM runs other prod services).
- `max_batches` argument reached (default: unlimited) → remind alert, STOP.
- User interrupt → mark in-flight batch status truthfully before stopping.

## Hard rules
- NEVER touch plan `global_constraints.do_not_touch` (schema/SSE/SM4/LangGraph-version/Docker-topology/
  frontend-stack/Gateway-contract). Gate + executor both enforce.
- Full suite green before every merge — no exceptions, no skips, no `-k` subsets as the gate.
- Heavy processes (pytest/npm/playwright/chrome) outside validate_full.sh: run via
  `systemd-run --user --collect --pipe` (session cgroup pids.max=512 — see qtrade-host-ops).
- Subagents: native Agent tool ONLY. NEVER spawn OMC teams / tmux panes / SendMessage
  teammates from the loop — pane churn crashed the tmux 3.4 server (libevent segfault,
  2026-07-17 01:20) and killed the whole session; panes also break on CLI auto-update (127).
- Do not restart `wechat-narrator.service` or touch GreenMail/we-mp-rss — unrelated prod.
- Keep code concise; reuse existing services/modules over new parallel implementations.
