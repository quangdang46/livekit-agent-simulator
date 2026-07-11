#!/usr/bin/env bash
# Install livekit-agent-simulator from GitHub (git + uv/pipx). No PyPI / wheel.
#
#   curl -fsSL "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.sh?$(date +%s)" | bash
#
# Options (bash -s -- …):
#   --version / --ref v0.1.0|main   git tag or branch (default: main)
#   --no-mcp                        skip MCP auto-config
#   --verify                        run lk-sim --help after install
#   --easy-mode                     ensure ~/.local/bin on PATH via shell rc
#   --quiet / -q
#   --uninstall
#   --help
#
set -euo pipefail
umask 022

BINARY_NAME="lk-sim"
MCP_BIN="livekit-agent-simulator-mcp"
MCP_SERVER_NAME="livekit-agent-simulator"
PKG_NAME="livekit-agent-simulator"
OWNER="quangdang46"
REPO="livekit-agent-simulator"
DEST="${DEST:-$HOME/.local/bin}"
GIT_REF="${LK_SIM_REF:-main}"
QUIET=0
EASY=0
VERIFY=0
UNINSTALL=0
NO_MCP=0
LOCK_DIR="${TMPDIR:-/tmp}/${BINARY_NAME}-install.lock.d"

log_info()    { [ "$QUIET" -eq 1 ] && return; echo "[${BINARY_NAME}] $*" >&2; }
log_warn()    { echo "[${BINARY_NAME}] WARN: $*" >&2; }
log_success() { [ "$QUIET" -eq 1 ] && return; echo "✓ $*" >&2; }
die()         { echo "ERROR: $*" >&2; exit 1; }

cleanup() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo $$ >"$LOCK_DIR/pid"
    return 0
  fi
  die "Another install is running. If stuck: rm -rf $LOCK_DIR"
}

usage() {
  cat <<EOF
Install ${PKG_NAME} from GitHub (uv tool / pipx). No PyPI.

  curl -fsSL "https://raw.githubusercontent.com/${OWNER}/${REPO}/main/install.sh?\$(date +%s)" | bash
  curl -fsSL "https://raw.githubusercontent.com/${OWNER}/${REPO}/main/install.sh?\$(date +%s)" | bash -s -- --ref v0.1.0 --verify
  curl -fsSL "https://raw.githubusercontent.com/${OWNER}/${REPO}/main/install.sh?\$(date +%s)" | bash -s -- --ref main --no-mcp
  curl -fsSL "https://raw.githubusercontent.com/${OWNER}/${REPO}/main/install.sh?\$(date +%s)" | bash -s -- --uninstall

Options:
  --version / --ref REF   git tag or branch (default: main)
  --no-mcp                skip MCP provider auto-config
  --easy-mode             append DEST to PATH in shell rc
  --verify                run ${BINARY_NAME} --help
  --quiet, -q
  --uninstall
  -h, --help
EOF
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version|--ref)   GIT_REF="$2"; shift 2 ;;
    --version=*|--ref=*) GIT_REF="${1#*=}"; shift ;;
    --from-git)        # accepted for back-compat; always git
      if [ $# -ge 2 ] && [[ "${2:-}" != -* ]]; then GIT_REF="$2"; shift 2; else shift; fi
      ;;
    --from-git=*)      GIT_REF="${1#*=}"; shift ;;
    --no-mcp)          NO_MCP=1; shift ;;
    --easy-mode)       EASY=1; shift ;;
    --verify)          VERIFY=1; shift ;;
    --quiet|-q)        QUIET=1; shift ;;
    --uninstall)       UNINSTALL=1; shift ;;
    -h|--help)         usage ;;
    *)                 shift ;;
  esac
done

maybe_add_path() {
  case ":$PATH:" in *":$DEST:"*) return 0 ;; esac
  if [ "$EASY" -eq 1 ]; then
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.zprofile"; do
      [ -f "$rc" ] && [ -w "$rc" ] || continue
      grep -qF "$DEST" "$rc" 2>/dev/null && continue
      printf '\nexport PATH="%s:$PATH"  # %s installer\n' "$DEST" "$BINARY_NAME" >>"$rc"
      log_info "PATH += $DEST in $rc"
    done
  fi
  log_warn "Ensure CLI on PATH: export PATH=\"$DEST:\$PATH\""
}

_json_merge() {
  local file="$1" key="$2" value="$3"
  mkdir -p "$(dirname "$file")"
  if [ ! -f "$file" ]; then
    printf '{\n  "%s": %s\n}\n' "$key" "$value" >"$file"
    return 0
  fi
  if command -v jq >/dev/null 2>&1; then
    local tmpf; tmpf="$(mktemp)"
    jq --argjson val "$value" --arg k "$key" '
      if has($k) then .[$k] = ((.[$k] // {}) + $val) else .[$k] = $val end
    ' "$file" >"$tmpf" && mv "$tmpf" "$file"
  elif command -v python3 >/dev/null 2>&1; then
    KEY="$key" VAL="$value" FILE="$file" python3 - <<'PY'
import json, os
f, k = os.environ["FILE"], os.environ["KEY"]
v = json.loads(os.environ["VAL"])
try:
    d = json.load(open(f, encoding="utf-8")) if os.path.getsize(f) else {}
except Exception:
    d = {}
if not isinstance(d, dict):
    d = {}
cur = d.get(k) if isinstance(d.get(k), dict) else {}
cur.update(v)
d[k] = cur
with open(f, "w", encoding="utf-8") as out:
    json.dump(d, out, indent=2)
    out.write("\n")
PY
  else
    log_warn "No jq/python3 — skip JSON merge for $file"
    return 1
  fi
}

_remove_mcp_from_file() {
  local file="$1" server="$2" parent="${3:-mcpServers}"
  [ -f "$file" ] || return 0
  if command -v jq >/dev/null 2>&1; then
    local tmpf; tmpf="$(mktemp)"
    jq --arg p "$parent" --arg s "$server" 'if .[$p] then del(.[$p][$s]) else . end' "$file" >"$tmpf" && mv "$tmpf" "$file"
  elif command -v python3 >/dev/null 2>&1; then
    FILE="$file" PARENT="$parent" SERVER="$server" python3 - <<'PY'
import json, os
f, p, s = os.environ["FILE"], os.environ["PARENT"], os.environ["SERVER"]
try:
    d = json.load(open(f, encoding="utf-8"))
except Exception:
    raise SystemExit(0)
if isinstance(d, dict) and isinstance(d.get(p), dict):
    d[p].pop(s, None)
    with open(f, "w", encoding="utf-8") as out:
        json.dump(d, out, indent=2)
        out.write("\n")
PY
  fi
}

_toml_upsert_mcp() {
  local file="$1" server_name="$2" command_path="$3"
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || touch "$file"
  if grep -q "^\[mcp_servers\.${server_name}\]" "$file" 2>/dev/null; then
    local tmpf; tmpf="$(mktemp)"
    awk -v sn="$server_name" -v cmd="$command_path" '
      BEGIN{insec=0}
      $0 ~ "^\\[mcp_servers\\." sn "\\]" {insec=1; print; next}
      insec && /^\[/ {insec=0}
      insec && /^command[[:space:]]*=/ {print "command = \"" cmd "\""; next}
      {print}
    ' "$file" >"$tmpf" && mv "$tmpf" "$file"
  else
    cat >>"$file" <<TOML

[mcp_servers.${server_name}]
type = "stdio"
command = "${command_path}"
args = []
TOML
  fi
}

_toml_remove_mcp() {
  local file="$1" server_name="$2"
  [ -f "$file" ] || return 0
  local tmpf; tmpf="$(mktemp)"
  awk -v sn="$server_name" '
    BEGIN{skip=0}
    $0 ~ "^\\[mcp_servers\\." sn "\\]" {skip=1; next}
    skip && /^\[/ {skip=0}
    skip {next}
    {print}
  ' "$file" >"$tmpf" && mv "$tmpf" "$file"
}

resolve_mcp_binary() {
  if command -v "$MCP_BIN" >/dev/null 2>&1; then
    command -v "$MCP_BIN"
    return 0
  fi
  for c in "$DEST/$MCP_BIN" "$HOME/.local/bin/$MCP_BIN"; do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

configure_mcp_provider() {
  local provider_name="$1" settings_file="$2" json_key="$3" binary="$4"
  [ -n "$binary" ] && [ -x "$binary" ] || return 0
  log_info "MCP: $provider_name → $settings_file"
  local mcp_entry
  mcp_entry=$(cat <<EOF
{
  "${MCP_SERVER_NAME}": {
    "command": "${binary}",
    "args": [],
    "env": {}
  }
}
EOF
)
  _json_merge "$settings_file" "$json_key" "$mcp_entry" || true
}

configure_mcp_opencode() {
  local binary="$1"
  local settings_file="$HOME/.opencode.json"
  [ ! -f "$settings_file" ] && [ -d "$HOME/.config/opencode" ] && settings_file="$HOME/.config/opencode/.opencode.json"
  [ -f "$settings_file" ] || [ -d "$(dirname "$settings_file")" ] || return 0
  local mcp_entry
  mcp_entry=$(cat <<EOF
{
  "${MCP_SERVER_NAME}": {
    "type": "stdio",
    "command": "${binary}",
    "args": [],
    "env": []
  }
}
EOF
)
  _json_merge "$settings_file" "mcpServers" "$mcp_entry" || true
}

configure_mcp_codex() {
  local binary="$1"
  local config_file="$HOME/.codex/config.toml"
  [ -d "$(dirname "$config_file")" ] || return 0
  log_info "MCP: Codex CLI → $config_file"
  _toml_upsert_mcp "$config_file" "$MCP_SERVER_NAME" "$binary"
}

configure_all_mcp_providers() {
  local mcp_binary
  mcp_binary=$(resolve_mcp_binary) || {
    log_warn "MCP binary not on PATH — skip provider config"
    return 0
  }

  configure_mcp_provider "Claude Code" "$HOME/.claude.json" "mcpServers" "$mcp_binary"
  configure_mcp_provider "Cursor" "$HOME/.cursor/mcp.json" "mcpServers" "$mcp_binary"

  local cline_settings
  case "$(uname -s)" in
    Darwin*)
      cline_settings="$HOME/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
      ;;
    *)
      cline_settings="$HOME/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
      ;;
  esac
  [ -d "$(dirname "$cline_settings")" ] && \
    configure_mcp_provider "Cline" "$cline_settings" "mcpServers" "$mcp_binary"

  configure_mcp_provider "Windsurf" "$HOME/.codeium/windsurf/mcp_config.json" "mcpServers" "$mcp_binary"
  configure_mcp_provider "VS Code Copilot" "$HOME/.vscode/mcp.json" "servers" "$mcp_binary"
  configure_mcp_provider "Gemini CLI" "$HOME/.gemini/settings.json" "mcpServers" "$mcp_binary"
  configure_mcp_provider "Amazon Q (CLI)" "$HOME/.aws/amazonq/mcp.json" "mcpServers" "$mcp_binary"
  configure_mcp_provider "Amazon Q (IDE)" "$HOME/.aws/amazonq/default.json" "mcpServers" "$mcp_binary"

  if [ -d ".warp" ] || [ -f "pyproject.toml" ]; then
    configure_mcp_provider "Warp" ".warp/.mcp.json" "mcpServers" "$mcp_binary"
  fi

  configure_mcp_opencode "$mcp_binary"
  configure_mcp_codex "$mcp_binary"
}

uninstall_mcp_providers() {
  _remove_mcp_from_file "$HOME/.claude.json" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file "$HOME/.cursor/mcp.json" "$MCP_SERVER_NAME" "mcpServers"
  local cline_settings
  case "$(uname -s)" in
    Darwin*)
      cline_settings="$HOME/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
      ;;
    *)
      cline_settings="$HOME/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
      ;;
  esac
  _remove_mcp_from_file "$cline_settings" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file "$HOME/.codeium/windsurf/mcp_config.json" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file "$HOME/.vscode/mcp.json" "$MCP_SERVER_NAME" "servers"
  _remove_mcp_from_file "$HOME/.gemini/settings.json" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file "$HOME/.aws/amazonq/mcp.json" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file "$HOME/.aws/amazonq/default.json" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file "$HOME/.opencode.json" "$MCP_SERVER_NAME" "mcpServers"
  _remove_mcp_from_file ".warp/.mcp.json" "$MCP_SERVER_NAME" "mcpServers"
  _toml_remove_mcp "$HOME/.codex/config.toml" "$MCP_SERVER_NAME"
}

do_uninstall() {
  log_info "Uninstalling ${PKG_NAME}..."
  if command -v uv >/dev/null 2>&1; then
    uv tool uninstall "$PKG_NAME" 2>/dev/null || true
  fi
  if command -v pipx >/dev/null 2>&1; then
    pipx uninstall "$PKG_NAME" 2>/dev/null || true
  fi
  rm -f "$DEST/$BINARY_NAME" "$DEST/$MCP_BIN" \
    "$HOME/.local/bin/$BINARY_NAME" "$HOME/.local/bin/$MCP_BIN" 2>/dev/null || true
  uninstall_mcp_providers
  for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.zprofile"; do
    [ -f "$rc" ] && sed -i.bak "/${BINARY_NAME} installer/d" "$rc" 2>/dev/null || true
    rm -f "${rc}.bak" 2>/dev/null || true
  done
  log_success "Uninstalled ${PKG_NAME}"
  exit 0
}

[ "$UNINSTALL" -eq 1 ] && do_uninstall

main() {
  acquire_lock
  mkdir -p "$DEST"

  log_info "Installing ${PKG_NAME} from git@${GIT_REF} (no PyPI)"
  log_info "Dest PATH hint: $DEST"

  local installer=""
  if command -v uv >/dev/null 2>&1; then
    installer="uv"
  elif command -v pipx >/dev/null 2>&1; then
    installer="pipx"
  else
    die "Need uv or pipx. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
  fi

  local spec="git+https://github.com/${OWNER}/${REPO}.git@${GIT_REF}"
  log_info "Source: $spec"

  if [ "$installer" = "uv" ]; then
    uv tool install --force "$spec"
  else
    pipx install --force "$spec"
  fi

  maybe_add_path

  if [ "$NO_MCP" -eq 0 ]; then
    configure_all_mcp_providers
  else
    log_info "Skipped MCP auto-config (--no-mcp)"
  fi

  if [ "$VERIFY" -eq 1 ]; then
    command -v "$BINARY_NAME" >/dev/null 2>&1 || die "${BINARY_NAME} not on PATH after install"
    "$BINARY_NAME" --help >/dev/null
    log_success "Verified ${BINARY_NAME} --help"
  fi

  echo ""
  log_success "${PKG_NAME} installed"
  if command -v "$BINARY_NAME" >/dev/null 2>&1; then
    echo "  CLI:  $(command -v "$BINARY_NAME")"
  fi
  if mcpb=$(resolve_mcp_binary 2>/dev/null); then
    echo "  MCP:  $mcpb"
  fi
  echo ""
  echo "  Quick start:"
  echo "    ${BINARY_NAME} guide"
  echo "    ${BINARY_NAME} init --root /path/to/target"
  echo "    ${BINARY_NAME} web --root /path/to/target"
  echo ""
  echo "  Report player is prebuilt in the git tree (no Node/pnpm required)."
  echo ""
}

if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" ]] || [[ -z "${BASH_SOURCE[0]:-}" ]]; then
  { main "$@"; }
fi
