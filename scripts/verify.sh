#!/usr/bin/env bash
# scripts/verify.sh [SHA] — owner-agent self-check for kureksistant.
# Runs a commit headlessly in an ISOLATED temp copy and prints PASS/FAIL.
#
# Safety (never touches a live install):
#   * code comes from `git archive <SHA>` into a temp dir (repo-relative
#     memory/kurek_memory.db of the live checkout is never opened)
#   * HOME, XDG_RUNTIME_DIR, XDG_CONFIG_HOME point into the temp dir
#     (no ~/.env keys, no live kurek.sock)
#   * daemon binds 127.0.0.1 only on a throwaway port (default 18791,
#     refuses 8790); KUREK_REMOTE_BIND/KUREK_BIND are unset
#   * tests/test_daemon_smoke.py is deselected because it hardcodes :8790;
#     step 2 replaces it on the throwaway port
#   * test_find_grok_desktop_file is deselected: it needs grok-bot.desktop
#     installed in the real ~/.local/share/applications (host-dependent)
# GUI/voice (mic, TTS, pyautogui, Hyprland) are NOT driven: not reliable
# headless. Step 2 boots the real daemon engine instead.
#
# Runs under `env -i` with an allowlist (PATH, HOME, LANG, TERM, DISPLAY, ...).
# Env: PYTHON (default: .venv/bin/python if present, else python3)
#      VERIFY_PORT (default 18791)  VERIFY_KEEP=1 keeps the temp dir
# Exit 0 = PASS. Writes verify-result.txt (SHA + PASS/FAIL) into $VERIFY_OUT if set.
set -uo pipefail
# Re-exec under an allowlisted environment so the commit under test cannot read
# ambient secrets (cloud/GitHub tokens, API keys, ...).
if [ -z "${VERIFY_SANITIZED:-}" ]; then
  keep=(VERIFY_SANITIZED=1 "PATH=$PATH" "HOME=$HOME" "LANG=${LANG:-C.UTF-8}" "TERM=${TERM:-dumb}")
  for v in PYTHON VERIFY_PORT VERIFY_OUT VERIFY_KEEP DISPLAY WAYLAND_DISPLAY XAUTHORITY; do
    [ -n "${!v:-}" ] && keep+=("$v=${!v}")
  done
  # resolve the real env(1): ~/.local/bin/env can be a non-executable shim (uv)
  exec "$(PATH=/usr/bin:/bin command -v env)" -i "${keep[@]}" bash "$0" "$@"
fi
REPO_ROOT=$(git rev-parse --show-toplevel) || { echo "FAIL: not a git repo"; exit 2; }
SHA=$(git -C "$REPO_ROOT" rev-parse "${1:-HEAD}^{commit}") || { echo "FAIL: bad rev"; exit 2; }
PORT=${VERIFY_PORT:-18791}
[[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] && [ "$PORT" -le 65535 ] || { echo "FAIL: bad VERIFY_PORT"; exit 2; }
[ "$PORT" -eq 8790 ] && { echo "FAIL: refusing live port 8790"; exit 2; }
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PYTHON="$REPO_ROOT/.venv/bin/python"; else PYTHON=python3; fi
fi
TMP=$(mktemp -d -t kurek-verify-XXXXXX)
DPID=""
cleanup(){
  [ -n "$DPID" ] && kill -TERM -- "-$DPID" 2>/dev/null
  sleep 0.5
  [ -n "$DPID" ] && kill -KILL -- "-$DPID" 2>/dev/null
  # Belt and braces: mpv is spawned by the daemon under test. If the daemon is
  # SIGKILLed it cannot run any shutdown hook, so reap anything still bound to
  # this run's temp runtime dir. Without this, each run orphaned one idle mpv
  # forever (observed: 20 leaked processes, ~250 MB, over 21 h).
  pkill -f -- "input-ipc-server=$TMP/run/kurek_mpv.sock" 2>/dev/null
  [ "${VERIFY_KEEP:-0}" = 1 ] || rm -rf "$TMP"
}
trap cleanup EXIT
ORIG_HOME=$HOME
mkdir -p "$TMP/src" "$TMP/home" "$TMP/run" "$TMP/cfg"; chmod 700 "$TMP/run"
git -C "$REPO_ROOT" archive "$SHA" | tar -x -C "$TMP/src"
cd "$TMP/src" || { echo "FAIL: cannot cd to $TMP/src"; exit 2; }
# some tests call `git ls-files`: give the copy its own throwaway git index
git init -q . && git add -A && git -c user.name=verify -c user.email=verify@localhost commit -qm "verify $SHA" >/dev/null
export HOME="$TMP/home" XDG_RUNTIME_DIR="$TMP/run" XDG_CONFIG_HOME="$TMP/cfg" PYTHONDONTWRITEBYTECODE=1
unset KUREK_REMOTE_BIND KUREK_BIND KUREK_REMOTE_ALLOW_EXEC KUREK_REMOTE_TOKEN \
  DEEPSEEK_API_KEY XAI_API_KEY GEMINI_API_KEY DEEPGRAM_API_KEY TYPESAFE_API_KEY OPENROUTER_API_KEY
# Deterministic gate: kurek_daemon.py does `import numpy` (which starts one
# OpenBLAS thread per core) before `import sounddevice`, which dlopen()s
# PortAudio. The glibc loader lock then races those fresh threads and the
# daemon deadlocks at import -- measured 7/30 starts hung (23%), each one
# surfacing here as a bogus `daemon not up` / `FAIL bind`. One BLAS thread
# removes the race without changing what the daemon does.
export OPENBLAS_NUM_THREADS=1
RES=(); ok=1
step(){ local name=$1; shift; if "$@" >"$TMP/$name.log" 2>&1; then RES+=("PASS $name"); else RES+=("FAIL $name (log: $TMP/$name.log)"); ok=0; VERIFY_KEEP=1; fi; }

# pyautogui/mouseinfo open $DISPLAY at import, and the temp HOME has no
# Xauthority. Prefer a private Xvfb; else reuse the session's Xauthority; else
# give Xlib an (empty) file so the import succeeds. Sets DISPLAY_PRE.
# Needed by BOTH the pytest step AND the daemon boot step: the daemon imports
# actions/computer_control.py -> pyautogui too, so without this it dies before
# it can bind and daemon_auth/bind fail.
DISPLAY_PRE=()
prepare_display(){
  DISPLAY_PRE=()
  if command -v xvfb-run >/dev/null; then
    DISPLAY_PRE=(/usr/bin/env -u DISPLAY -u WAYLAND_DISPLAY xvfb-run -a)
  elif [ -n "${DISPLAY:-}" ]; then
    local xa=${XAUTHORITY:-$ORIG_HOME/.Xauthority}
    if [ -f "$xa" ]; then export XAUTHORITY="$xa"; else : >"$HOME/.Xauthority"; fi
  fi
}

# 1. test suite (minus the :8790 smoke test)
run_tests(){
  prepare_display
  "${DISPLAY_PRE[@]}" "$PYTHON" -m pytest -q -p no:cacheprovider \
    --deselect tests/test_daemon_smoke.py \
    --deselect tests/test_open_app_grokbot.py::test_find_grok_desktop_file \
    tests
}
step tests run_tests

# 2+3. daemon boot on throwaway port + token auth (loopback only)
TOKEN=$("$PYTHON" -c 'import secrets;print(secrets.token_hex(32))')
cat > "$TMP/boot.py" <<PY
import runpy, sys
mod = runpy.run_path("kurek_daemon.py", run_name="kurek_verify")
mod["run_server"](mod["KurekEngine"](), port=int(sys.argv[1]))
PY
daemon_and_auth(){
  prepare_display
  KUREK_REMOTE_TOKEN="$TOKEN" setsid "${DISPLAY_PRE[@]}" "$PYTHON" -u "$TMP/boot.py" "$PORT" >"$TMP/daemon.out" 2>&1 &
  DPID=$!
  PORT="$PORT" TOKEN="$TOKEN" "$PYTHON" - <<'PY'
import json, os, time, urllib.request, urllib.error
base = f"http://127.0.0.1:{os.environ['PORT']}"
tok = os.environ["TOKEN"]
def req(path, method="GET", hdr=None, body=None):
    r = urllib.request.Request(base + path, method=method, data=body, headers=hdr or {})
    try:
        with urllib.request.urlopen(r, timeout=3) as f: return f.status, f.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
deadline = time.time() + 90
while True:
    try:
        s, b = req("/status"); break
    except Exception as e:
        if time.time() > deadline: raise SystemExit(f"daemon not up: {e}")
        time.sleep(0.5)
st = json.loads(b); print("status", s, st)
assert s == 200 and st.get("state") == "idle", st
j = {"Content-Type": "application/json"}
s, _ = req("/remote/prompt", "POST", j, b"{}"); print("no token ->", s); assert s == 401
s, _ = req("/remote/prompt", "POST", {**j, "Authorization": "Bearer " + "x"*64}, b"{}"); print("bad token ->", s); assert s == 401
# valid token + empty prompt: auth passes, handler returns 400 before running anything
s, _ = req("/remote/prompt", "POST", {**j, "Authorization": "Bearer " + tok}, b"{}"); print("good token ->", s); assert s == 400
print("auth OK (200 status / 401 missing+bad / authorized 400 no-op)")
PY
}
step daemon_auth daemon_and_auth
grep -q "Listening at http://127.0.0.1:$PORT" "$TMP/daemon.out" || { RES+=("FAIL bind (no loopback bind on $PORT; daemon died or hung at startup -- see $TMP/daemon.out)"); ok=0; }
grep -Eq "Listening at http://([0-9.]+):" "$TMP/daemon.out" && grep -Eo "Listening at http://[0-9.]+" "$TMP/daemon.out" | grep -qv 127.0.0.1 && { RES+=("FAIL bind (non-loopback host)"); ok=0; }

# 4. memory migration on a temp DB
migrate(){ "$PYTHON" - <<'PY'
import json, sqlite3, tempfile, pathlib
from memory import memory_manager as mm
d = pathlib.Path(tempfile.mkdtemp())
mm.DB_PATH = d / "m.db"; mm.MEMORY_PATH = d / "long_term.json"
mm.MEMORY_PATH.write_text(json.dumps({
  "preferences": {"pet": {"value": "orange cat", "updated": "2026-01-01"}},
  "sessions": [{"date": "2026-01-02", "summary": "verify session", "language": "en"}]}))
mem = mm.load_memory()
assert mem["preferences"]["pet"]["value"] == "orange cat", mem
c = sqlite3.connect(mm.DB_PATH)
assert c.execute("select value from metadata where key='migrated'").fetchone()[0] == "1"
assert c.execute("select count(*) from sessions").fetchone()[0] == 1
mm.load_memory()  # idempotent: no duplicate sessions on second open
assert c.execute("select count(*) from sessions").fetchone()[0] == 1
print("migration OK")
PY
}
step migration migrate

RESULT=$([ $ok = 1 ] && echo PASS || echo FAIL)
{ echo "VERIFY $RESULT sha=$SHA"; printf '  %s\n' "${RES[@]}"; } | tee "${VERIFY_OUT:-/dev/null}"
[ $ok = 1 ]
