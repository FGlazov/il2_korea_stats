# Pending decisions: core.logparse

Decisions made while implementing `core.logparse` that the design docs didn't specify. For the lead to merge into
`11_open_questions.md` / `05_technical_decisions.md`.

- **AType 27/28 are "ignored", not "unknown".** They're documented in doc 12 and deliberately dropped (maintainer, 2026-10-02), so they
  become a `GenericEvent` and are counted in `ParseStats.ignored_atypes`, not `unknown_atypes`. The never-seen 17, 22, 23, 29 (and anything
  new) count as unknown, because their appearance is news. Where: `parser.IGNORED_ATYPES`, `parse_lines`. (Additive new field
  `ParseStats.ignored_atypes`.)
- **Plain `[0].txt`: raw part or whole-mission archive?** The names are identical, so the caller decides: `group_mission_files(paths,
  txt_as="parts" | "archive")`, default `"parts"` (production reads raw parts, FR-ING-1); history import passes `"archive"`. A `[N].txt`
  with N >= 1 always marks the mission as raw parts (its `[0].txt` is then part 0 whatever `txt_as` says). `.txt.zip` is always an archive.
  Where: `files.group_mission_files`.
- **Precedence when several inputs exist for one mission UID**: raw parts beat any archive (they're newer); a plain `.txt` archive beats a
  `.txt.zip` (same content, no decompression). Same path twice counts once; two paths with the same UID and N keep the first in sorted path
  order. Names not matching `missionReport(YYYY-MM-DD_HH-MM-SS)[N].txt[.zip]` are ignored (`*.weather.json`, etc.).
- **Mission UID** = the timestamp inside the parentheses, as a string, e.g. `2026-09-19_22-34-13` (doc 06). Parts sort numerically by N.
- **Free-text values are anchored on the next known key** of that AType (`NAME`, `TYPE`, `SKIN` in 10; `TYPE`, `NAME` in 12; `MFile` in 0),
  matching ` KEY:` or ` KEY(`. A nickname that itself contains e.g. ` TYPE:` would be cut there; we accept that (never seen in 18M lines).
  Where: `parser._Spec.terminators`, `tokenize`.
- **A bad line is any line with a malformed token, a missing required key, an unconvertible value, a duplicate key, or unbalanced
  parentheses.** It raises `ParseError` from `parse_line` and is counted in `lines_bad` (with a warning, max 200 warnings per mission,
  each quoting at most 200 chars of the line) from `parse_lines`. Where: `parser.MAX_WARNINGS`, `MAX_WARNING_LINE_CHARS`.
- **Unknown ATypes never fail**, even when their text can't be tokenized: the remainder is kept under `GenericEvent.fields["#raw"]`.
- **Unknown keys**: any token not read by the AType's spec goes to `extra`. It's counted in `unknown_keys` as `"<atype>:<KEY>"` unless it's a
  documented trailing key (`MID` on 12, `ROUNDS`/`POINTS` on 0, `TARGETS()/OBJECTS()/PLANES()/MTARGETS()/MOBJETS()` on 8, `IDS()` on 9),
  which still sit in `extra` but are not "unknown". Where: `_Spec.known_extra`.
- **Blank lines are skipped and not counted** in `lines_total`. Lines are stripped, so CRLF and trailing spaces are fine.
- **`KEY: value` (space after the colon)** takes the next word as the value unless that word is itself a key, so `MID: GType:2` gives
  `MID=""` and `ROUNDS: 1` gives `1`.
- **`stats.log_version`** is the first AType 15 `VER` of the mission. A later different `VER` adds a warning (not a bad line).
- **Encoding**: UTF-8 with `errors="replace"`, a leading BOM is dropped, line endings normalized. A stray byte can at most change one value.
- **A zip without exactly one `.txt` raises `ValueError`** from `read_mission_lines` (a file-level problem, not a line problem; the ingester
  should mark that mission failed).
- **Speed**: all 210 sample missions (1,235 MB, 18.06M lines) parse in 155 s, about 8 MB/s; the slowest mission took 2.5 s, a median
  ~6 MB mission about 0.7 s.
