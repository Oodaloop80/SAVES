#!/bin/sh
# SAVES — NAS pre-flight check. Run from the repo root ON THE NAS, before the first
# `docker-compose up`. Verifies the host mounts, secrets, cookies, state dir, and the
# workstation Whisper server so a misconfigured deploy fails HERE (fast + readable)
# instead of half-way through a container start. Read-only: it changes nothing.
#
#   sh scripts/preflight_nas.sh                 # parse Whisper URL from config.yaml
#   sh scripts/preflight_nas.sh http://IP:5000/health   # or pass it explicitly
#
# Exit 0 = all green. Exit 1 = at least one blocker (WARN lines never fail).
#
# POSIX sh (busybox-safe) so it runs under Synology's default shell. docker/.env is
# parsed by grep — NOT sourced — because VAULT_HOST legitimately contains a space
# ("Remote Vault"), which `.`-sourcing would split into a bogus command.

set -u
FAIL=0
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=1; }
ok()   { printf '  \033[32m OK \033[0m  %s\n' "$1"; }

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 2
echo "SAVES pre-flight — repo: $ROOT"
echo

# --- 1. docker + compose ----------------------------------------------------
echo "[1] Docker engine + compose"
if command -v docker >/dev/null 2>&1; then ok "docker present"; else bad "docker not on PATH (enable Container Manager / SSH as an admin, or 'sudo')"; fi
if docker compose version >/dev/null 2>&1; then ok "'docker compose' (v2) present"
elif command -v docker-compose >/dev/null 2>&1; then ok "'docker-compose' (v1) present"
else bad "neither 'docker compose' nor 'docker-compose' found"; fi

# --- 2. secrets (.env) ------------------------------------------------------
echo "[2] Secrets (repo-root .env)"
if [ -f .env ]; then
  for k in ANTHROPIC_API_KEY DISCORD_BOT_TOKEN; do
    v=$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)
    if [ -n "$v" ]; then ok "$k set"; else bad "$k missing/empty in .env"; fi
  done
else bad ".env not found at repo root (cp .env.example .env, then fill the 2 keys)"; fi

# --- 3. host paths (docker/.env) -------------------------------------------
echo "[3] Host mounts (docker/.env)"
if [ -f docker/.env ]; then
  get_env() { grep -E "^$1=" docker/.env | head -1 | cut -d= -f2-; }
  VAULT_HOST=$(get_env VAULT_HOST)
  MEDIA_HOST=$(get_env MEDIA_HOST)
  STATE_HOST=$(get_env STATE_HOST)
  check_dir() {
    if [ -z "$2" ]; then bad "$1 not set in docker/.env"; return; fi
    if [ -d "$2" ]; then ok "$1 → $2"; else bad "$1 → $2 (directory does NOT exist)"; fi
  }
  check_dir VAULT_HOST "$VAULT_HOST"
  check_dir MEDIA_HOST "$MEDIA_HOST"
  check_dir STATE_HOST "$STATE_HOST"
  # State MUST be a writable directory (never a single-file bind: os.replace() onto a
  # file mountpoint fails with EBUSY, and Docker auto-creates a dir if the file is absent).
  if [ -n "$STATE_HOST" ] && [ -d "$STATE_HOST" ]; then
    if touch "$STATE_HOST/.saves_write_test" 2>/dev/null; then
      rm -f "$STATE_HOST/.saves_write_test"; ok "STATE_HOST is writable"
    else bad "STATE_HOST not writable by this user (fix ownership/permissions)"; fi
  fi
  # Vault must be writable (notes are written into it) + the inbox dir should exist.
  if [ -n "$VAULT_HOST" ] && [ -d "$VAULT_HOST" ]; then
    if touch "$VAULT_HOST/.saves_write_test" 2>/dev/null; then
      rm -f "$VAULT_HOST/.saves_write_test"; ok "VAULT_HOST is writable"
    else bad "VAULT_HOST not writable by this user (notes can't be saved)"; fi
    if [ -d "$VAULT_HOST/0 - INBOX" ]; then ok "inbox dir exists"
    else warn "inbox dir missing: $VAULT_HOST/0 - INBOX (watcher waits until it exists)"; fi
  fi
else bad "docker/.env not found (cp docker/.env.example docker/.env, set the *_HOST vars)"; fi

# --- 4. cookies -------------------------------------------------------------
echo "[4] Platform cookies"
n=$(ls cookies/*.txt 2>/dev/null | wc -l | tr -d ' ')
if [ "${n:-0}" -gt 0 ]; then ok "$n cookie file(s) in cookies/"
else warn "no cookies/*.txt — Instagram/TikTok/Facebook will fail (Reddit/YouTube/web are fine)"; fi
# The cookies dir must be WRITABLE: an authenticated site login is a Chromium *profile*
# dir Playwright writes to (compose mounts cookies :rw). A read-only cookies dir breaks
# /crawl + login-gated generic extraction even though the .txt files themselves are static.
if [ -d cookies ]; then
  if touch cookies/.saves_write_test 2>/dev/null; then
    rm -f cookies/.saves_write_test; ok "cookies/ is writable (browser profiles need this)"
  else bad "cookies/ NOT writable — /crawl + login-gated sites can't launch the browser profile"; fi
fi
# Provecho crawl needs the authenticated persistent profile (captured on a machine with a
# browser, then copied here — the NAS is headless). Warn if it's absent or looks empty.
prof=$(ls -d cookies/*_profile 2>/dev/null | head -1)
if [ -n "$prof" ] && [ -d "$prof" ]; then
  if [ -d "$prof/Default" ] || [ -f "$prof/Default/Preferences" ] || [ -n "$(ls -A "$prof" 2>/dev/null)" ]; then
    ok "browser profile present: $prof"
  else warn "$prof exists but looks empty — /crawl provecho auth will fail"; fi
else warn "no cookies/<host>_profile/ — /crawl (provecho) needs one; copy it from a workstation capture"; fi

# --- 5. Whisper reachability ------------------------------------------------
echo "[5] Whisper server (workstation)"
url=${1:-$(grep -E "remote_url:" config.yaml | head -1 | sed -E 's/.*"(http[^"]+)".*/\1/')}
health=$(printf '%s' "$url" | sed -E 's#/transcribe#/health#')
if [ -n "$health" ]; then
  if curl -fsS --max-time 5 "$health" >/dev/null 2>&1; then ok "reachable: $health"
  else bad "NOT reachable: $health — start the workstation Whisper server AND open inbound TCP 5000 in Windows Firewall"; fi
else warn "could not parse transcription.remote_url from config.yaml"; fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mPre-flight PASSED — safe to run: docker compose up --build -d\033[0m\n'
else
  printf '\033[31mPre-flight FAILED — fix the FAIL lines above before deploying\033[0m\n'
fi
exit $FAIL
