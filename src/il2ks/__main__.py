"""`python -m il2ks ...`: same as the `il2ks` command. `il2ks run` starts its child processes this way, because it
needs no PATH lookup and always means the Python environment il2ks is installed in."""

from il2ks.cli import main

if __name__ == "__main__":  # the guard matters: web-server workers re-import the main module when they start
    raise SystemExit(main())
