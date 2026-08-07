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

# --- 6. NAS co-tenancy: memory headroom + Synology compose traps -------------
# Since 2026-08-05 this NAS also hosts the Forgejo forge (Forgejo 2g + PostgreSQL 1g).
# SAVES is no longer alone, so an uncapped Chromium can OOM a neighbour. Two Synology
# specifics are hard deploy failures rather than warnings — see docs/FORGEJO.md §6.
echo "[6] NAS resources + Synology compose traps"

# 6a. A CPU quota is REJECTED BY THE DAEMON on Synology (no CFS bandwidth control):
#     "NanoCPUs can not be set, as your kernel does not support CPU CFS scheduler".
if grep -Eq '^[[:space:]]*(cpus|cpu_quota|cpu_period):' docker/docker-compose.yml 2>/dev/null; then
  bad "docker-compose.yml sets a CPU quota — Synology kernels REJECT it and the deploy will fail. Remove it."
else
  ok "no CPU quota in compose (required on Synology)"
fi

# 6b. A top-level `version:` key makes Compose V2 discard the v2-style mem_limit.
if grep -Eq '^version:' docker/docker-compose.yml 2>/dev/null; then
  bad "docker-compose.yml has a top-level 'version:' key — it makes Compose V2 ignore mem_limit. Remove it."
else
  ok "no top-level 'version:' key"
fi

# 6c. Memory headroom. mem_limit only protects the host if it leaves room for DSM and
#     for whatever else is running (Forgejo/Postgres today).
mem_kb=$(grep -E '^MemTotal:' /proc/meminfo 2>/dev/null | awk '{print $2}')
lim=$(grep -E '^SAVES_MEM_LIMIT=' docker/.env 2>/dev/null | head -1 | cut -d= -f2-)
[ -z "$lim" ] && lim="3g"
# normalise "3g" / "3G" / "3072m" -> MB
# normalise to whole MB — int() so the $(( )) arithmetic below never sees a float
lim_mb=$(printf '%s' "$lim" | awk '
  /[gG]$/ { sub(/[gG]$/,""); print int($0 * 1024); next }
  /[mM]$/ { sub(/[mM]$/,""); print int($0);        next }
  { print int($0 / 1048576) }')
[ -n "$lim_mb" ] || lim_mb=0
if [ -n "$mem_kb" ]; then
  total_mb=$((mem_kb / 1024))
  ok "NAS RAM: ${total_mb} MB total; SAVES_MEM_LIMIT=${lim} (~${lim_mb} MB)"
  # Sum the memory limits already claimed by other running containers.
  other_mb=0
  if command -v docker >/dev/null 2>&1; then
    for b in $(docker ps -q 2>/dev/null); do
      nm=$(docker inspect -f '{{.Name}}' "$b" 2>/dev/null | sed 's#^/##')
      [ "$nm" = "saves_app" ] && continue
      mb=$(docker inspect -f '{{.HostConfig.Memory}}' "$b" 2>/dev/null)
      [ -n "$mb" ] && [ "$mb" -gt 0 ] 2>/dev/null && other_mb=$((other_mb + mb / 1048576))
    done
    [ "$other_mb" -gt 0 ] && ok "other containers reserve ~${other_mb} MB (e.g. forgejo + forgejo-db)"
  fi
  # Leave ~1.5 GB for DSM itself.
  if [ $((lim_mb + other_mb + 1536)) -gt "$total_mb" ]; then
    warn "over-committed: SAVES ${lim_mb} MB + others ${other_mb} MB + ~1536 MB for DSM > ${total_mb} MB RAM. Lower SAVES_MEM_LIMIT in docker/.env or add RAM."
  else
    ok "memory headroom looks sane"
  fi
else
  warn "could not read /proc/meminfo — check RAM headroom manually (docs/FORGEJO.md §6)"
fi

# 6d. Compose file must actually parse with the host's compose binary.
if docker compose version >/dev/null 2>&1; then
  if docker compose -f docker/docker-compose.yml config >/dev/null 2>&1; then
    ok "'docker compose config' parses the stack"
  else
    bad "'docker compose -f docker/docker-compose.yml config' FAILED — run it to see the error before deploying"
  fi
fi

# --- 7. Identity: container UID/GID vs host directory ownership -------------
# The container runs NON-ROOT as SAVES_UID:SAVES_GID (docker/.env). Three things must
# agree: the DSM account, docker/.env, and the owner of every directory it writes. If they
# disagree the container starts and then fails every write with EACCES — which surfaces as
# "notes never appear, no errors". Catch it here instead. Map: PROD_ROLLOUT.md §1.6.
echo "[7] Container identity vs directory ownership"
if [ -f docker/.env ]; then
  S_UID=$(grep -E '^SAVES_UID=' docker/.env | head -1 | cut -d= -f2- | tr -d ' ')
  S_GID=$(grep -E '^SAVES_GID=' docker/.env | head -1 | cut -d= -f2- | tr -d ' ')
  if [ -z "$S_UID" ] || [ -z "$S_GID" ]; then
    bad "SAVES_UID/SAVES_GID not set in docker/.env — the container would run as root and write root-owned notes into your vault"
  else
    ok "docker/.env declares ${S_UID}:${S_GID}"
    # Does that UID actually exist on this host? (A typo'd UID "works" until it can't write.)
    if command -v getent >/dev/null 2>&1; then
      nm=$(getent passwd "$S_UID" 2>/dev/null | cut -d: -f1)
    else
      nm=$(awk -F: -v u="$S_UID" '$3==u {print $1}' /etc/passwd 2>/dev/null | head -1)
    fi
    if [ -n "$nm" ]; then ok "UID $S_UID resolves to '$nm' on this host"
    else warn "UID $S_UID has no /etc/passwd entry — fine for Docker, but confirm it matches 'id sa_saves'"; fi

    # No group_add: vault access comes from a DSM ACL naming sa_saves (matched by UID),
    # not group membership. Granting the everyone-group `users` anything is forbidden
    # (NAS_SERVICE_ACCOUNTS.md Rule 1), so flag it if it reappears in compose.
    if grep -qE '^[[:space:]]*-[[:space:]]*"?100"?[[:space:]]*$' docker/docker-compose.yml 2>/dev/null \
       && grep -q 'group_add' docker/docker-compose.yml 2>/dev/null; then
      bad "docker-compose.yml group_add includes GID 100 (users) — that is the everyone-group and is forbidden. Grant sa_saves via a DSM ACL instead (NAS_SERVICE_ACCOUNTS.md Rule 1/4)."
    else
      ok "compose grants no 'users' membership"
    fi

    # --- Every /volume1 share on this NAS is ACL-managed --------------------
    # Verified 2026-08-06: APPS, MEDIA and docker all report has_ACL. So ownership is
    # DELIBERATELY OodaAdmin:administrators everywhere (SOP Rule 2) and the container is
    # granted by a named ACE instead. Checking POSIX owner here would produce false FAILs.
    # `fd--` inheritance means one ACE on /volume1/docker/saves covers app/, cookies/,
    # logs/ and state/ — we check the project root rather than each leaf.
    check_acl() {
      label="$1"; d="$2"; want="$3"     # want = read | write
      [ -e "$d" ] || return 0            # missing paths already reported by [3]
      if ! command -v synoacltool >/dev/null 2>&1; then
        warn "$label: synoacltool unavailable (not DSM?) — verify with the container write test, SOP 5.1"
        return 0
      fi
      acl=$(synoacltool -get "$d" 2>/dev/null)
      if [ -z "$acl" ]; then
        warn "$label: could not read the ACL (try sudo) — verify manually per SOP 5.1"
        return 0
      fi
      allow=$(printf '%s\n' "$acl" | grep -n "user:${SA_NAME}:allow" | head -1 | cut -d: -f1)
      deny=$(printf '%s\n' "$acl" | grep -n 'group:docker_service_accounts:deny' | head -1 | cut -d: -f1)
      if [ -z "$allow" ]; then
        bad "$label: no 'user:${SA_NAME}:allow' ACE. Every tree here is default-deny, so SAVES gets nothing. Add one (SOP Rule 4 / 6)."
        return 0
      fi
      if [ -n "$deny" ] && [ "$deny" -lt "$allow" ]; then
        bad "$label: a docker_service_accounts DENY sorts BEFORE the ${SA_NAME} allow -> blocked. The allow must be explicitly set (level:0) on this folder, not inherited (SOP Rule 4)."
        return 0
      fi
      if [ "$want" = "write" ]; then
        line=$(printf '%s\n' "$acl" | grep "user:${SA_NAME}:allow" | head -1)
        case "$line" in
          *rw*) ok "$label: ${SA_NAME} allow ACE with write" ;;
          *) bad "$label: ${SA_NAME}'s ACE is read-only but SAVES must WRITE here. Regrant with rwxpdDaARWc--" ;;
        esac
      else
        ok "$label: ${SA_NAME} allow ACE (read)"
      fi
      # Rule 1: the everyone-group must never be granted.
      printf '%s\n' "$acl" | grep -q 'group:users:allow' && \
        bad "$label: the ACL grants group 'users' — the everyone-group is forbidden (SOP Rule 1)."
      # Secrets must not be reachable by the other container's account.
      case "$label" in
        "saves project tree")
          printf '%s\n' "$acl" | grep -q 'user:sa_forgejo:allow' && \
            bad "$label: sa_forgejo is ALLOWED here — it would be able to read SAVES's .env and cookies." ;;
      esac
      return 0
    }

    SA_NAME=${nm:-sa_saves}
    # Three distinct vault grants (SOP 6). The vault ROOT is read-only ON PURPOSE — tag_index
    # walks the whole vault but never writes there; write lands in the two subfolders only.
    check_acl "vault root"         "$VAULT_HOST"                   read
    check_acl "vault inbox"        "$VAULT_HOST/0 - INBOX/SAVES"   write
    check_acl "vault notes root"   "$VAULT_HOST/SAVES"             write
    check_acl "media"              "$MEDIA_HOST"                   write
    # One ACE on the project root covers state/, cookies/ and logs/ via fd-- inheritance.
    check_acl "saves project tree" "/volume1/docker/saves"         write
    check_acl "state"              "$STATE_HOST"                   write
  fi
else
  bad "docker/.env missing — cannot verify container identity"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mPre-flight PASSED — safe to run: docker compose up --build -d\033[0m\n'
else
  printf '\033[31mPre-flight FAILED — fix the FAIL lines above before deploying\033[0m\n'
fi
exit $FAIL
