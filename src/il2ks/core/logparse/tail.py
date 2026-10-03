"""Incremental reading of a mission's raw parts while DServer is still writing them (FR-ING-12).

`PartTail` remembers a byte offset per part and hands back only the lines added since the last call. A line counts
only once its newline is written, so a half-written last line is picked up whole on the next call. When a new part
appears, the previous part is closed (read to its very end) and the new one starts at its first byte.

Anything that breaks the "files only grow, parts only append" assumption (a part shrank or vanished, a part appeared
*before* one already read) makes `read_new` return `None`: the caller starts over with a fresh `PartTail`, which
re-reads every part from the start. The same path serves a restart of the watch process, which simply begins with an
empty `PartTail`.
"""

import re
from collections.abc import Sequence
from pathlib import Path

from il2ks.core.logparse.files import LOG_ENCODING

_BOM = "﻿"
_LINE_BREAKS = re.compile(r"\r\n|\r|\n")


class PartTail:
    """Reads the new lines of a mission's parts, in part order, since the previous `read_new`."""

    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}  # part name -> bytes consumed (complete lines only)

    def read_new(self, parts: Sequence[Path]) -> list[str] | None:
        """New complete lines of `parts` (sorted by part number). None: the files changed under us, start over.

        Blank lines are returned as they are (the parser skips them). A UTF-8 byte order mark at the start of a part
        is dropped, as in `files.read_mission_lines`."""
        names = [p.name for p in parts]
        if not set(self._offsets) <= set(names):
            return None  # a part we already read is gone
        known = [n for n in names if n in self._offsets]
        new = [n for n in names if n not in self._offsets]
        if known and any(names.index(n) < names.index(known[-1]) for n in new):
            return None  # a part appeared before one we already read: order would break
        lines: list[str] = []
        for position, path in enumerate(parts):
            is_last = position == len(parts) - 1
            offset = self._offsets.get(path.name, 0)
            try:
                size = path.stat().st_size
                if size < offset:
                    return None  # shrunk: replaced or truncated
                if size == offset:
                    self._offsets.setdefault(path.name, offset)
                    continue
                with path.open("rb") as stream:
                    stream.seek(offset)
                    data = stream.read(size - offset)
            except OSError:
                return None  # moved away while we looked; the caller re-discovers
            end = len(data) if not is_last else data.rfind(b"\n") + 1  # a closed part has no unfinished line
            if end == 0:
                self._offsets.setdefault(path.name, offset)
                continue
            text = data[:end].decode(LOG_ENCODING, errors="replace")
            if offset == 0:
                text = text.removeprefix(_BOM)
            chunk = _LINE_BREAKS.split(text)
            if chunk and chunk[-1] == "":
                chunk.pop()
            lines.extend(chunk)
            self._offsets[path.name] = offset + end
        return lines
