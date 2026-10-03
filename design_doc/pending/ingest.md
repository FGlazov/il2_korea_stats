# Pending decisions: ingest jobs (discovery, archive, lock, runner, CLI, config)

Decisions made while implementing the ingest jobs that the design docs didn't specify. For the lead to merge into
`11_open_questions.md` / `05_technical_decisions.md` / `07_deployment_and_installation.md`.

## Config (`config.py`)

- **Config sources, later wins**: defaults, then `il2ks.toml`, then `IL2KS_<SECTION>_<KEY>` env vars (`[logs] dir` is `IL2KS_LOGS_DIR`; top-level
  keys drop the section: `IL2KS_DATA_DIR`, `IL2KS_LOG_LEVEL`). Lists are comma-separated in env vars. Why: TD-11 says env overrides for Docker.
  Where: `config._Reader`.
- **Which file**: `--config`, else `IL2KS_CONFIG`, else `./il2ks.toml`, else `<data dir>/il2ks.toml`, else none (defaults + env). Relative paths
  in the file resolve against the file's folder, in env vars against the working directory. Where: `config.find_config_file`.
- **Sections and defaults** (all in `Config`): `logs.dir` (none: `ingest`/`watch` refuse to run without `--from`), `logs.after_archive = "move"`,
  `logs.move_to` (default `<data dir>/ingested-logs/<mission uid>/`), `logs.remote = false`; `ingest.idle_minutes = 10`, `ingest.settle_seconds = 60`,
  `ingest.stable_seconds = 60`, `ingest.watch_interval_s = 30`, `ingest.retry_backoff_minutes = [5, 30, 120]`; `[replay]` takes every `ReplayRules`
  field by name; `[server] timezone`, `[server] uid`; `log_level = "INFO"`; `data_dir`.
- **Server UID storage (TD-17)**: `[server] uid` if set; else `<data dir>/server_uid.txt`, generated (uuid4) on first use and then kept. Why: the
  future `il2ks setup` can copy it into `il2ks.toml`; until then it must be stable across runs. Where: `config.stored_server_uid`.
- **Server timezone default (TD-15)**: `[server] timezone`, else the `TZ` env var, else the `/etc/localtime` symlink (Linux), else `UTC`. Windows
  has no IANA name without a Windows-to-IANA table, so Windows admins set `[server] timezone` (or run DServer on UTC, doc 07). `il2ks doctor`
  should warn when a Windows machine is on UTC by fallback. Where: `config.detect_os_timezone`.
- **Completeness has one extra knob, `ingest.settle_seconds` (60)**: AType 7 is followed by cleanup lines (doc 12 Timing: only 14 of 210 files end
  with 7), so a mission with AType 7 counts as complete only once no part was written for this long. Where: `discover.completeness`.

## Discovery (`discover.py`)

- **Completeness order**: remote mode first requires every part unmodified for `stable_seconds` (a newer mission existing doesn't prove the copy
  finished, FR-ING-16); then "newer mission's `[0]` exists", then idle >= `idle_minutes`, then AType 7 (searched as raw bytes `AType:7` not followed
  by a digit, in the last 3 parts only) plus `settle_seconds`. Whole-mission archives are complete at once (after the stability check in remote
  mode). Where: `discover.completeness`, `MISSION_END_SCAN_PARTS`.
- **Fingerprint** = sha256 over `(file name, size, mtime_ns)` of the mission's files in order; folder-independent (FR-ING-18). A re-copy with new
  mtimes counts as "changed" and re-ingests (harmless: upsert). Where: `discover.fingerprint`.
- **Run history classification** (`discover.classify`): no run -> new; last ok/skipped -> unchanged or changed (fingerprint); last failed -> retry
  when `next_retry_at` has passed, or at once when the fingerprint or the il2ks version differs; failed with no `next_retry_at` -> "gave up" (stays
  failed until files change, a new version is installed, or `reprocess --mission`). Skipped decisions are not recorded as `IngestRun` rows
  (they'd flood the table every watch tick); they're counted in the run summary and logged.
- **Retry accounting**: `attempts` counts consecutive failures with the same fingerprint and version; any change resets it to 1. Schedule: attempt 1
  fails -> retry in 5 min; 2 -> 30 min; 3 -> 2 h; 4 -> stop (`next_retry_at = NULL`). So a mission gets at most 4 automatic attempts. Where:
  `discover.next_retry`, `runner.ingest_mission`.

## Archive (`archive.py`)

- **Layout**: `<data dir>/archive/YYYY/MM/missionReport(<uid>)[0].txt.zip` (YYYY/MM from the mission UID, i.e. the server's local time), one zip entry
  `missionReport(<uid>)[0].txt` with all parts concatenated in order. Same names as `sample_data/` and the il2_stats backups, so
  `group_mission_files` reads our archives like any import (FR-ING-13). DEFLATE level 6 (stdlib, readable everywhere). `IngestRun.archive_path` is
  stored relative to the data dir (portable when the data dir moves), with `/` separators.
- **Checksums**: `IngestRun.archive_sha256` is the sha256 of the zip file itself (reconcile and reprocess compare it); the writer also verifies the
  zip CRCs and a sha256 of the uncompressed content, in a temp file, before atomically renaming it into place. A failed verification leaves any
  earlier archive untouched.
- **Line break between parts**: if a part doesn't end with a newline, a CRLF is inserted so lines never merge (never seen in samples; defensive).
- **Late parts after the originals were moved (FR-ING-18)**: the new archive = the previous archive (must still match its recorded checksum) + the
  new parts, so it holds the whole mission. If the previous archive is gone, the mission fails with a clear error. Parts that reappear although they
  are already archived are not appended twice (warning on the run). Where: `runner.plan_sources`.
- **After-archive handling**: only after the DB commit, so a crash between never loses anything; failures to move (Windows: file still open) are
  collected, logged, and retried on the next run, because "unchanged" missions still get their leftover originals disposed. `move` puts files in
  `<move_to>/<mission uid>/` and replaces a leftover with the same name. `ingest --from` **never** moves, deletes, or otherwise touches its source,
  whatever `logs.after_archive` says (there is no flag to change that). Where: `archive.dispose_originals`, `runner._ingest_locked`.
- **Reconcile (FR-ING-8)**: an ingested mission whose source files are still in the log folder (keep mode, or a failed move) but whose archive file
  is missing is re-ingested from the sources. It runs on every ingest (it's one `is_file()` per mission), so no separate startup phase, and it checks
  existence, not the checksum, to keep a tick cheap with thousands of kept files. If the sources are gone too, nothing can be done and `reprocess`
  reports the mission as failed ("archive missing or changed"). `watch` does not disable it.

## Lock (`lock.py`)

- **OS file lock, not a PID check**: `<data dir>/writer.lock` is locked with `msvcrt.locking` (Windows) / `fcntl.flock` (Linux, macOS). The OS drops
  the lock when the holder dies, so a stale lock can't happen; the file's JSON (PID, host, command, since) only feeds the "who holds it" message and a
  "replacing stale lock" warning. Why: PID-liveness checks are racy and PID reuse breaks them. The lock byte on Windows is far past the content so
  the info stays readable.
- **A second writer exits at once** with exit code 3 and the message naming the holder (`--wait SECONDS` waits instead). `watch` takes the lock
  **per tick**, not for its lifetime, so an admin can run `reprocess` / `rebuild-aggregates` while `watch` is up (the tick is skipped and logged).
  Where: `lock.WriterLock`, `watch.watch`.

## Runner, reprocess, CLI

- **Pipeline injection**: `runner.Pipeline` (group, parse, replay, save, resolve_start); `default_pipeline(cfg)` wires the real ones and loads the
  catalog once per process, lazily. Tests inject fakes.
- **One `IngestRun` row per attempt that did work**, `ok` or `failed`, never for skipped missions. Failed runs keep the archive path/sha when the
  archive step succeeded, the traceback in `error`, and the parse counters seen so far. The OK run is saved inside the same `transaction.atomic()`
  as `save_mission`, so a mission and its run commit together.
- **Parse reads the archive**, not the originals (the archive is the source of truth, doc 04). `ParseStats` counters are filled while `replay`
  consumes the generator, and copied to the run after it.
- **TD-15 hint**: for raw parts the `[0]` part's mtime is passed to `resolve_mission_start`; for imports no hint (the zip entry time is in an unknown
  time zone). `warnings` from it are stored on the run.
- **Migrations (FR-OPS-3)**: every writer command (`ingest`, `watch`, `reprocess`, `rebuild-aggregates`) applies pending migrations at start, under the
  writer lock, before doing anything. `watch` checks once at start, not per tick.
- **Exit codes**: 0 ok; 1 done but some missions failed (or `reprocess --mission` named a mission with no archive); 2 usage/config error; 3 lock held.
- **`il2ks reprocess`**: missions come from the newest archive-writing `IngestRun` of each mission (so failed missions with an archive can be forced),
  **plus archives in `<data dir>/archive/` that have no `IngestRun` at all** (rebuilding from a lost DB, FR-ING-9; these get `fingerprint = ""` so a
  later `ingest` of the log folder sees them as changed and redoes them once). Workers (`ProcessPoolExecutor`, default CPUs-1, below-normal priority
  via `SetPriorityClass` / `os.nice(10)`) import only the core, no Django. The main process is the only writer, with a bounded window of
  `2 x workers` results in flight. A reprocess failure never schedules a retry (`next_retry_at = NULL`) and leaves the previous good rows in place.
  `rebuild_aggregates()` runs once at the end, only if something succeeded.
- **`IngestRun.fingerprint` of a reprocess run** is copied from the run it reprocesses, so discovery doesn't see a change.
- **`ingest --from <dir|file>`**: groups with `txt_as="archive"` (a lone `[0].txt` is a whole mission), treats every mission as complete, keeps sources
  as-is, and is idempotent through the same fingerprint (name, size, mtime), so re-importing the same folder skips everything.
- **Windows tests of the lock** use real child processes (one killed mid-lock); Linux `fcntl` path is untested here.

## Needs from other areas / not done

- `Mission.completed_cleanly` comes from `MissionResult.mission.completed_cleanly` (AType 7 seen) via persist; ingest doesn't pass the completeness
  reason (`idle` vs `mission_end`) anywhere. If the admin page should show "completed only by idle timeout", `IngestRun` needs a field for it
  (not added: no model changes in this area).
- No `il2ks.toml` template or `il2ks setup` (planned command stubs remain).
