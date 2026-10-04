#!/bin/sh
# Entrypoint of the il2ks image. Everything is configured by IL2KS_* environment variables (no il2ks.toml needed;
# a /data/il2ks.toml is still read if you add one). On `run` (the default command) the first start also creates the
# admin account from IL2KS_ADMIN_USERNAME + IL2KS_ADMIN_PASSWORD (or IL2KS_ADMIN_PASSWORD_FILE, for Docker secrets),
# but only while no admin exists: changing the variables later never resets a password.
# Any other command is passed to il2ks: `docker compose run --rm il2ks doctor`, `... backup`, `... createadmin`.
set -eu

# The browser setup page (/setup/) only answers a browser on the same machine as il2ks, which a container's never is, so
# it is switched off here (IL2KS_SETUP_PAGE=off, no token is made). Say clearly what to do instead.
announce_createadmin() {
    {
        echo ""
        echo "*** il2ks: no admin account was created ($1)."
        echo "*** There is no browser setup page in Docker. Create the admin with:"
        echo "***   docker compose -f docker/compose.yaml exec il2ks il2ks createadmin"
        echo "*** (or set IL2KS_ADMIN_USERNAME and IL2KS_ADMIN_PASSWORD in docker/.env and restart; the admin is only"
        echo "*** created while none exists)."
        echo ""
    } >&2
}

bootstrap_admin() {
    if [ -z "${IL2KS_ADMIN_USERNAME:-}" ]; then
        announce_createadmin "no IL2KS_ADMIN_USERNAME is set"
        return 0
    fi
    if [ -n "${IL2KS_ADMIN_PASSWORD_FILE:-}" ]; then
        set -- --password-file "$IL2KS_ADMIN_PASSWORD_FILE"
    elif [ -n "${IL2KS_ADMIN_PASSWORD:-}" ]; then
        set --
    else
        announce_createadmin "IL2KS_ADMIN_USERNAME is set without IL2KS_ADMIN_PASSWORD (or _FILE)"
        return 0
    fi
    # A weak password or a busy database must not keep the site from starting (restart loops help nobody).
    il2ks createadmin --if-none --username "$IL2KS_ADMIN_USERNAME" "$@" ||
        echo "il2ks: the admin account was NOT created (see above); fix the password and restart." >&2
}

case "${1:-run}" in
    run)
        export IL2KS_SETUP_PAGE=off
        bootstrap_admin
        # The password was only needed for the line above: the server and its children must not inherit it (it would be
        # readable in /proc/<pid>/environ and in every crash report that dumps the environment).
        unset IL2KS_ADMIN_PASSWORD
        exec il2ks run
        ;;
    sh | bash)
        exec "$@"
        ;;
    *)
        exec il2ks "$@"
        ;;
esac
