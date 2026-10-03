"""How `il2ks doctor` shows its findings: grouped text (coloured when the terminal can), or JSON for tools.

Exit code of the command: 0 = everything OK, 1 = warnings only, 2 = at least one error.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping
from typing import TextIO

from il2ks.ops.doctor import Finding, Level

_COLORS: dict[Level, str] = {Level.OK: "32", Level.WARN: "33", Level.ERROR: "31"}  # green, yellow, red
_ORDER = (Level.ERROR, Level.WARN, Level.OK)  # what needs attention first


def exit_code(findings: Iterable[Finding]) -> int:
    """0 OK, 1 warnings only, 2 errors."""
    return int(max((f.level for f in findings), default=Level.OK))


def _enable_windows_vt() -> bool:
    """Switch the Windows console to understand ANSI colours (Windows 10 and later)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def use_color(stream: TextIO, env: Mapping[str, str] | None = None) -> bool:
    """Colour only on a real terminal, never when NO_COLOR is set (https://no-color.org) or TERM is dumb."""
    env = os.environ if env is None else env
    if env.get("NO_COLOR") or env.get("TERM") == "dumb" or not stream.isatty():
        return False
    return _enable_windows_vt()


def render_text(findings: list[Finding], *, color: bool) -> str:
    def paint(level: Level, text: str) -> str:
        return f"\x1b[{_COLORS[level]}m{text}\x1b[0m" if color else text

    lines: list[str] = []
    for level in _ORDER:
        group = [f for f in findings if f.level is level]
        if not group:
            continue
        lines.append(paint(level, f"{level.name} ({len(group)})"))
        for f in group:
            lines.append(f"  {paint(level, '[' + level.name + ']')} {f.title}")
            if f.detail:
                lines.append(f"      {f.detail}")
            if f.fix:
                lines.append(f"      What to do: {f.fix}")
        lines.append("")
    errors = sum(f.level is Level.ERROR for f in findings)
    warnings = sum(f.level is Level.WARN for f in findings)
    if errors:
        verdict = f"{errors} error(s), {warnings} warning(s): fix the errors first."
    elif warnings:
        verdict = f"No errors, {warnings} warning(s)."
    else:
        verdict = "Everything looks fine."
    lines.append(paint(max((f.level for f in findings), default=Level.OK), verdict))
    return "\n".join(lines)


def render_json(findings: list[Finding]) -> str:
    return json.dumps(
        {
            "exit_code": exit_code(findings),
            "findings": [{"level": f.level.name, "title": f.title, "detail": f.detail, "fix": f.fix} for f in findings],
        },
        indent=2,
    )
