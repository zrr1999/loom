#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
LAYOUT_FILE="${REPO_ROOT}/loom-layout.kdl"
LOOM_DIR="${REPO_ROOT}/.loom"
RUNTIME_DIR="${LOOM_DIR}/runtime/zellij"
BOOT_LOG="${RUNTIME_DIR}/bootstrap.log"
PANES_JSON="${RUNTIME_DIR}/panes.json"
TABS_JSON="${RUNTIME_DIR}/tabs.json"
SESSION_ENV="${RUNTIME_DIR}/session.env"
INSTRUCTIONS_TXT="${RUNTIME_DIR}/instructions.txt"
WEB_TXT="${RUNTIME_DIR}/web.txt"

hash_path() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "${REPO_ROOT}" | sha256sum | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        printf '%s' "${REPO_ROOT}" | shasum -a 256 | awk '{print $1}'
    else
        printf '%s' "${REPO_ROOT}" | openssl dgst -sha256 | awk '{print $2}'
    fi
}

SESSION_HASH="$(hash_path | cut -c1-8)"
SESSION="${LOOM_ZELLIJ_SESSION:-loom_${SESSION_HASH}}"

usage() {
    cat <<EOF
Usage: ./start.sh <command> [options]

Commands:
  up [--no-attach] [--reset] [--web] [--read-only-token]
      Create or reuse the repo-local Zellij session, capture metadata, and optionally attach.
  list-panes
      Refresh and print pane metadata as JSON.
  list-tabs
      Refresh and print tab metadata as JSON.
  dump-pane <pane-id>
      Dump a pane viewport+scrollback to stdout.
  subscribe-pane <pane-id> [pane-id...]
      Subscribe to live render updates for one or more panes.
  send-pane [--no-enter] <pane-id> <text...>
      Write text into a pane and optionally press Enter.
  watch
      Attach to the session in read-only mode.
  web-start [--read-only-token]
      Start the local Zellij web server and optionally mint a read-only token.
  metadata
      Print the generated session environment file.

Environment:
  LOOM_ZELLIJ_SESSION      Override the generated session name.
  LOOM_ZELLIJ_IP           Web server listen IP (default: 127.0.0.1).
  LOOM_ZELLIJ_PORT         Web server listen port (default: 8082).
  LOOM_ZELLIJ_CERT         TLS cert for non-localhost web sharing.
  LOOM_ZELLIJ_KEY          TLS key for non-localhost web sharing.
EOF
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "missing required command: $1" >&2
        exit 1
    fi
}

session_exists() {
    zellij list-sessions 2>/dev/null \
        | sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g' \
        | awk '{print $1}' \
        | grep -Fxq "${SESSION}"
}

require_session() {
    if ! session_exists; then
        echo "session ${SESSION} does not exist; run ./start.sh up first" >&2
        exit 1
    fi
}

ensure_runtime_dir() {
    mkdir -p "${RUNTIME_DIR}"
}

wait_for_session() {
    local attempt
    for attempt in $(seq 1 160); do
        if session_exists; then
            return 0
        fi
        sleep 0.25
    done
    echo "timed out waiting for Zellij session ${SESSION}" >&2
    if [[ -f "${BOOT_LOG}" ]]; then
        echo "--- bootstrap log ---" >&2
        cat "${BOOT_LOG}" >&2
    fi
    exit 1
}

capture_metadata() {
    require_session
    ensure_runtime_dir
    close_default_tab_if_present
    zellij --session "${SESSION}" action list-panes --json --all --tab --state --command --geometry >"${PANES_JSON}"
    zellij --session "${SESSION}" action list-tabs --json --all --panes --state --layout >"${TABS_JSON}"

    cat >"${SESSION_ENV}" <<EOF
LOOM_ZELLIJ_SESSION=${SESSION}
LOOM_ZELLIJ_REPO_ROOT=${REPO_ROOT}
LOOM_ZELLIJ_LAYOUT=${LAYOUT_FILE}
LOOM_ZELLIJ_RUNTIME_DIR=${RUNTIME_DIR}
LOOM_ZELLIJ_PANES_JSON=${PANES_JSON}
LOOM_ZELLIJ_TABS_JSON=${TABS_JSON}
LOOM_ZELLIJ_BOOT_LOG=${BOOT_LOG}
EOF

    cat >"${INSTRUCTIONS_TXT}" <<EOF
Session: ${SESSION}
Repo: ${REPO_ROOT}

Common commands:
  ./start.sh list-tabs
  ./start.sh list-panes
  ./start.sh dump-pane <pane-id>
  ./start.sh subscribe-pane <pane-id>
  ./start.sh send-pane <pane-id> "uv run loom status"
  ./start.sh watch

Direct Zellij automation:
  zellij --session "${SESSION}" action list-panes --json --all --tab --state --command --geometry
  zellij --session "${SESSION}" action dump-screen --full --pane-id <pane-id>
  zellij --session "${SESSION}" action write-chars --pane-id <pane-id> "..."
  zellij --session "${SESSION}" action send-keys --pane-id <pane-id> Enter
  zellij --session "${SESSION}" subscribe --pane-id <pane-id> --format json --scrollback 200

Remote/read-only:
  ./start.sh watch
  ./start.sh web-start --read-only-token

Known caveat:
  Zellij may keep its bootstrap "Tab #1" in detached mode before the first interactive attach.
  If needed: zellij --session "${SESSION}" action close-tab-by-id 0
EOF
}

has_loom_layout() {
    zellij --session "${SESSION}" action list-tabs --json --all 2>/dev/null | python - <<'PY'
import json
import sys

try:
    tabs = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if any(tab.get("name") == "director" for tab in tabs) else 1)
PY
}

close_default_tab_if_present() {
    local default_tab_id
    default_tab_id="$(
        zellij --session "${SESSION}" action list-tabs --json --all 2>/dev/null | python - <<'PY'
import json
import sys

try:
    tabs = json.load(sys.stdin)
except Exception:
    sys.exit(0)
default_tabs = [tab["tab_id"] for tab in tabs if tab.get("name") == "Tab #1"]
if len(tabs) > 1 and default_tabs:
    print(default_tabs[0])
PY
    )" || true

    if [[ -n "${default_tab_id}" ]]; then
        zellij --session "${SESSION}" action close-tab-by-id "${default_tab_id}" >/dev/null 2>&1 || true
        sleep 0.25
    fi

    return 0
}

load_layout_if_needed() {
    if has_loom_layout; then
        return 0
    fi

    zellij --session "${SESSION}" --layout "${LAYOUT_FILE}" >>"${BOOT_LOG}" 2>&1
    sleep 0.5
    close_default_tab_if_present
    return 0
}

print_session_summary() {
    cat <<EOF
Zellij session ready: ${SESSION}
  runtime: ${RUNTIME_DIR}
  panes:   ${PANES_JSON}
  tabs:    ${TABS_JSON}

Next:
  ./start.sh list-panes
  ./start.sh watch

Note:
  Detached bootstrap may still show Zellij's default "Tab #1" until the first interactive attach.
EOF
}

start_web() {
    local create_read_only_token=0
    while (($#)); do
        case "$1" in
            --read-only-token)
                create_read_only_token=1
                ;;
            *)
                echo "unknown web-start option: $1" >&2
                exit 1
                ;;
        esac
        shift
    done

    ensure_runtime_dir

    local ip="${LOOM_ZELLIJ_IP:-127.0.0.1}"
    local port="${LOOM_ZELLIJ_PORT:-8082}"
    local -a web_cmd=(zellij web --start --daemonize --ip "${ip}" --port "${port}")

    if [[ "${ip}" != "127.0.0.1" ]]; then
        : "${LOOM_ZELLIJ_CERT:?LOOM_ZELLIJ_CERT is required for non-localhost web sharing}"
        : "${LOOM_ZELLIJ_KEY:?LOOM_ZELLIJ_KEY is required for non-localhost web sharing}"
        web_cmd+=(--cert "${LOOM_ZELLIJ_CERT}" --key "${LOOM_ZELLIJ_KEY}")
    fi

    "${web_cmd[@]}" >/dev/null

    {
        echo "web server: https://${ip}:${port}"
        zellij web --status || true
    } >"${WEB_TXT}"

    if ((create_read_only_token)); then
        {
            echo
            echo "read-only token:"
            zellij web --create-read-only-token --token-name "${SESSION}-readonly"
        } >>"${WEB_TXT}"
    fi

    cat "${WEB_TXT}"
}

start_session() {
    local attach=1
    local reset=0
    local enable_web=0
    local create_read_only_token=0

    while (($#)); do
        case "$1" in
            --no-attach)
                attach=0
                ;;
            --reset)
                reset=1
                ;;
            --web)
                enable_web=1
                ;;
            --read-only-token)
                create_read_only_token=1
                ;;
            *)
                echo "unknown up option: $1" >&2
                exit 1
                ;;
        esac
        shift
    done

    require_cmd zellij
    require_cmd bash

    ensure_runtime_dir
    cd "${REPO_ROOT}"

    if ((reset)) && session_exists; then
        zellij kill-session "${SESSION}" >/dev/null 2>&1 || true
        zellij delete-session "${SESSION}" >/dev/null 2>&1 || true
        sleep 0.5
    fi

    if ! session_exists; then
        : >"${BOOT_LOG}"
        zellij attach --create --create-background --forget "${SESSION}" >>"${BOOT_LOG}" 2>&1 || true
        wait_for_session
    fi

    load_layout_if_needed
    capture_metadata

    if ((enable_web)); then
        local -a web_args=()
        if ((create_read_only_token)); then
            web_args+=(--read-only-token)
        fi
        start_web "${web_args[@]}"
    fi

    print_session_summary

    if ((attach)); then
        exec zellij attach "${SESSION}"
    fi
}

list_panes() {
    capture_metadata
    cat "${PANES_JSON}"
}

list_tabs() {
    capture_metadata
    cat "${TABS_JSON}"
}

dump_pane() {
    require_session
    local pane_id="${1:-}"
    [[ -n "${pane_id}" ]] || { echo "usage: ./start.sh dump-pane <pane-id>" >&2; exit 1; }
    zellij --session "${SESSION}" action dump-screen --full --pane-id "${pane_id}"
}

subscribe_pane() {
    require_session
    (($# >= 1)) || { echo "usage: ./start.sh subscribe-pane <pane-id> [pane-id...]" >&2; exit 1; }
    zellij --session "${SESSION}" subscribe --format json --scrollback 200 --pane-id "$@"
}

send_pane() {
    require_session
    local send_enter=1
    if [[ "${1:-}" == "--no-enter" ]]; then
        send_enter=0
        shift
    fi

    local pane_id="${1:-}"
    shift || true
    (($# >= 1)) || { echo "usage: ./start.sh send-pane [--no-enter] <pane-id> <text...>" >&2; exit 1; }

    zellij --session "${SESSION}" action write-chars --pane-id "${pane_id}" "$*"
    if ((send_enter)); then
        zellij --session "${SESSION}" action send-keys --pane-id "${pane_id}" Enter
    fi
}

show_metadata() {
    capture_metadata
    cat "${SESSION_ENV}"
}

watch_session() {
    require_session
    exec zellij watch "${SESSION}"
}

main() {
    local command="${1:-up}"
    shift || true

    case "${command}" in
        up)
            start_session "$@"
            ;;
        list-panes)
            list_panes
            ;;
        list-tabs)
            list_tabs
            ;;
        dump-pane)
            dump_pane "$@"
            ;;
        subscribe-pane)
            subscribe_pane "$@"
            ;;
        send-pane)
            send_pane "$@"
            ;;
        watch)
            watch_session
            ;;
        web-start)
            start_web "$@"
            ;;
        metadata)
            show_metadata
            ;;
        help|-h|--help)
            usage
            ;;
        *)
            echo "unknown command: ${command}" >&2
            usage >&2
            exit 1
            ;;
    esac
}

main "$@"
