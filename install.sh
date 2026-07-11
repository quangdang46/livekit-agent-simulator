#!/usr/bin/env bash
# Install livekit-agent-simulator (lk-sim) for the current user.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh | bash
set -euo pipefail

REPO="${LK_SIM_REPO:-https://github.com/quangdang46/livekit-agent-simulator.git}"
REF="${LK_SIM_REF:-main}"

echo "==> livekit-agent-simulator installer"
echo "    repo: $REPO"
echo "    ref:  $REF"

if command -v uv >/dev/null 2>&1; then
  echo "==> Installing with uv tool (recommended)"
  uv tool install --force "git+${REPO}@${REF}"
  echo ""
  echo "Done. Try:"
  echo "  lk-sim guide"
  echo "  lk-sim init"
  echo "  lk-sim web"
  exit 0
fi

if command -v pipx >/dev/null 2>&1; then
  echo "==> uv not found; installing with pipx"
  pipx install --force "git+${REPO}@${REF}"
  echo ""
  echo "Done. Try: lk-sim guide"
  exit 0
fi

echo "Neither uv nor pipx found."
echo "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
echo "Then re-run this installer."
exit 1
