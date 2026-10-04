#!/usr/bin/env bash
# Smoke test of the il2ks image (used by CI, runnable by hand):   docker/smoke-test.sh [image]   (default il2ks:local)
# Needs docker and curl. Starts throw-away containers, checks them, removes them.
#   1. `il2ks --version` and `il2ks doctor --json` run as the non-root user.
#   2. The default command (`il2ks run`: web + Caddy, self-signed certificate because no domain is set) comes up:
#      the site answers over https, http redirects, the admin from IL2KS_ADMIN_* exists, the healthcheck turns healthy,
#      and a restart keeps the admin instead of resetting it.
#   3. `IL2KS_HTTPS_MODE=external` (no Caddy) serves the production settings on the web port.
set -euo pipefail

IMAGE="${1:-il2ks:local}"
PREFIX="il2ks-smoke-$$"
HTTPS_PORT="${SMOKE_HTTPS_PORT:-18443}"
HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
WEB_PORT="${SMOKE_WEB_PORT:-18000}"
ADMIN_PASSWORD='Smoke-Test-Passw0rd-Zq81'

fail() { echo "SMOKE TEST FAILED: $*" >&2; exit 1; }

cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        for name in "$PREFIX-full" "$PREFIX-external"; do
            if docker inspect "$name" >/dev/null 2>&1; then
                echo "----- logs of $name -----" >&2
                docker logs --tail 80 "$name" >&2 2>&1 || true
            fi
        done
    fi
    docker rm -f "$PREFIX-full" "$PREFIX-external" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# wait_for SECONDS DESCRIPTION CURL_ARGS...
wait_for() {
    local seconds="$1" what="$2"
    shift 2
    local deadline=$((SECONDS + seconds))
    until curl -fsS -o /dev/null --max-time 5 "$@" 2>/dev/null; do
        [ "$SECONDS" -lt "$deadline" ] || fail "$what did not come up within ${seconds}s"
        sleep 2
    done
    echo "ok: $what"
}

wait_healthy() {
    local name="$1" deadline=$((SECONDS + ${2:-180}))
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$name")" = "healthy" ]; do
        [ "$SECONDS" -lt "$deadline" ] || fail "$name did not become healthy"
        sleep 3
    done
    echo "ok: $name is healthy"
}

echo "== 1. one-off commands"
version="$(docker run --rm "$IMAGE" --version)"
echo "$version"
case "$version" in il2ks\ *) ;; *) fail "unexpected --version output: $version" ;; esac
[ "$(docker run --rm "$IMAGE" sh -c 'id -u')" != "0" ] || fail "the image runs as root"
docker run --rm -e IL2KS_SERVER_TIMEZONE=UTC "$IMAGE" doctor --json >/dev/null && doctor_status=0 || doctor_status=$?
[ "$doctor_status" -le 2 ] || fail "il2ks doctor crashed (exit $doctor_status)"
echo "ok: doctor ran (exit $doctor_status)"

echo "== 2. il2ks run (web + Caddy)"
docker run -d --name "$PREFIX-full" \
    -p "$HTTPS_PORT:443" -p "$HTTP_PORT:80" \
    -e IL2KS_SERVER_TIMEZONE=UTC \
    -e IL2KS_ADMIN_USERNAME=smoke -e IL2KS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    "$IMAGE" >/dev/null
wait_for 120 "https via Caddy" -k --resolve "localhost:$HTTPS_PORT:127.0.0.1" "https://localhost:$HTTPS_PORT/"
wait_for 30 "admin login page" -k --resolve "localhost:$HTTPS_PORT:127.0.0.1" "https://localhost:$HTTPS_PORT/admin/login/"
redirect="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$HTTP_PORT/")"
case "$redirect" in 301 | 302 | 307 | 308) echo "ok: http redirects ($redirect)" ;; *) fail "http answered $redirect, not a redirect" ;; esac
# Not `docker logs | grep -q`: grep -q exits at the first match, docker logs dies of SIGPIPE, and pipefail then fails the
# pipeline at random (the flaky "admin account was not created").
logs="$(docker logs "$PREFIX-full" 2>&1)"
grep -q "Admin account 'smoke': created" <<<"$logs" || fail "the admin account was not created"
echo "ok: admin created from IL2KS_ADMIN_*"
wait_healthy "$PREFIX-full"
docker restart "$PREFIX-full" >/dev/null
wait_for 120 "https after a restart" -k --resolve "localhost:$HTTPS_PORT:127.0.0.1" "https://localhost:$HTTPS_PORT/"
logs="$(docker logs "$PREFIX-full" 2>&1)"
grep -q "An admin account already exists" <<<"$logs" || fail "the restart did not find the admin"
echo "ok: the restart kept the data"
docker rm -f "$PREFIX-full" >/dev/null

echo "== 3. external mode (no Caddy)"
docker run -d --name "$PREFIX-external" -p "$WEB_PORT:8000" \
    -e IL2KS_SERVER_TIMEZONE=UTC -e IL2KS_HTTPS_MODE=external -e IL2KS_WEB_HOST=0.0.0.0 \
    "$IMAGE" >/dev/null
wait_for 120 "web server (as behind a proxy)" -H 'X-Forwarded-Proto: https' "http://127.0.0.1:$WEB_PORT/"
processes="$(docker top "$PREFIX-external")"
if grep -q caddy <<<"$processes"; then fail "Caddy runs in external mode"; fi
echo "ok: no Caddy in external mode"

echo "SMOKE TEST PASSED"
