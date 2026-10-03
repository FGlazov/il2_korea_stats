#!/bin/sh
# Entrypoint of the il2ks image. Everything is configured by IL2KS_* environment variables (no il2ks.toml needed;
# a /data/il2ks.toml is still read if you add one). On `run` (the default command) the first start also creates the
# admin account from IL2KS_ADMIN_USERNAME + IL2KS_ADMIN_PASSWORD (or IL2KS_ADMIN_PASSWORD_FILE, for Docker secrets),
# but only while no admin exists: changing the variables later never resets a password.
# Any other command is passed to il2ks: `docker compose run --rm il2ks doctor`, `... backup`, `... createadmin`.
set -eu

bootstrap_admin() {
    if [ -z "${IL2KS_ADMIN_USERNAME:-}" ]; then
        echo "il2ks: no IL2KS_ADMIN_USERNAME set. Create the admin once the site is up with:" >&2
        echo "       docker compose exec il2ks il2ks createadmin" >&2
        return 0
    fi
    if [ -n "${IL2KS_ADMIN_PASSWORD_FILE:-}" ]; then
        set -- --password-file "$IL2KS_ADMIN_PASSWORD_FILE"
    elif [ -n "${IL2KS_ADMIN_PASSWORD:-}" ]; then
        set --
    else
        echo "il2ks: IL2KS_ADMIN_USERNAME is set without IL2KS_ADMIN_PASSWORD (or _FILE): no admin created." >&2
        return 0
    fi
    # A weak password or a busy database must not keep the site from starting (restart loops help nobody).
    il2ks createadmin --if-none --username "$IL2KS_ADMIN_USERNAME" "$@" ||
        echo "il2ks: the admin account was NOT created (see above); fix the password and restart." >&2
}

case "${1:-run}" in
    run)
        bootstrap_admin
        exec il2ks run
        ;;
    sh | bash)
        exec "$@"
        ;;
    *)
        exec il2ks "$@"
        ;;
esac
