"""Exit codes of the `il2ks` command (FR-OPS-1). `il2ks doctor` uses 0, 1 and 2 as documented in `ops.report`."""

EXIT_OK = 0  # done
EXIT_FAILED = 1  # done, but some missions failed (ingest, reprocess); doctor: warnings only
EXIT_USAGE = 2  # usage or configuration error, a refused action; doctor: errors found
EXIT_LOCKED = 3  # another writer holds the lock (FR-ING-20)
