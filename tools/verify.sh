#!/usr/bin/env bash
# Verification command (D5). Uses the deterministic stub model.
set -euo pipefail
export LLM_PROVIDER=stub
export PYTHONIOENCODING=utf-8
python "$(dirname "$0")/verify.py" "$@"
