#!/bin/zsh
# Double-click in Finder, or run this file from a terminal.
set -u
cd -- "${0:A:h}" || exit 1
TASK_PY="${SOCIALDOG_PYTHON:-}"
if [[ -z "$TASK_PY" ]]; then
  if [[ -x .venv/bin/python ]]; then
    TASK_PY="$PWD/.venv/bin/python"
  elif [[ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]]; then
    TASK_PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  else
    TASK_PY="$(command -v python3)" || exit 1
  fi
fi
"$TASK_PY" chat_agent.py --check
TASK_CHECK_STATUS=$?
if [[ "$TASK_CHECK_STATUS" -eq 2 ]]; then
  "$TASK_PY" chat_agent.py --setup || exit 1
elif [[ "$TASK_CHECK_STATUS" -ne 0 ]]; then
  exit "$TASK_CHECK_STATUS"
fi
exec "$TASK_PY" web_agent.py
