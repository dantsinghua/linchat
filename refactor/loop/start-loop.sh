#!/usr/bin/env bash
# Hardened launcher for the unattended refactor loop.
# Why this exists (2026-07-17 crash postmortem):
#   1. `claudex` alias caps the scope at MemoryMax=3G / TasksMax=512 — the loop
#      (main claude + subagents + claude-mem headless workers + build tools) blew
#      through it: fork EAGAIN 01:09, headless SIGABRT 01:11.
#   2. CLI auto-updated mid-run (2.1.202 -> 2.1.211 at 01:17); an earlier update
#      already killed subagent panes with status 127 (deleted old binary).
#   3. tmux server segfaulted at 01:20:00 (libevent_core) and took the whole
#      interactive session down with it.
# Mitigations here: pin the binary for the run, disable auto-update, give the
# loop its own scope with realistic caps (still protective of this 8G prod VM),
# and make sure the tmux server is NOT a child of this scope.
set -euo pipefail

# tmux server must pre-exist OUTSIDE our scope; if we are about to fork it from
# inside claude's cgroup it shares our memory cap and its crash kills the loop.
if [ -n "${TMUX:-}" ]; then
    :  # already inside tmux — server predates us, fine
elif ! tmux ls >/dev/null 2>&1; then
    echo "WARN: no tmux server running. Start the loop inside an existing tmux" >&2
    echo "      session (tmux new -s loop) so its server lives outside this scope." >&2
fi

# Resolve the CURRENT version binary and pin it: symlink churn during a run is
# what produced the 'Pane is dead (status 127)' failures.
CLAUDE_BIN=$(readlink -f "$(command -v claude)")
[ -x "$CLAUDE_BIN" ] || { echo "claude binary not found" >&2; exit 1; }
echo "loop claude binary pinned to: $CLAUDE_BIN"

FREE_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
[ "$FREE_MB" -lt 1500 ] && { echo "ABORT: MemAvailable ${FREE_MB}M < 1500M — host too loaded for a loop run" >&2; exit 1; }

exec systemd-run --user --scope -q \
    -p MemoryMax=4500M -p MemoryHigh=4G -p TasksMax=1024 \
    --setenv=DISABLE_AUTOUPDATER=1 \
    -- "$CLAUDE_BIN" --dangerously-skip-permissions "$@"
