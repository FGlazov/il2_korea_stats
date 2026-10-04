"""Reference for the installer's `QuoteArg` (il2ks.iss): one command-line argument for Windows' standard parser.

The installer starts `il2ks setup ...` with paths the admin typed or picked, for example the game's log folder. A path
that ends in a backslash (`D:\\IL-2\\logs\\`, what the folder picker and Explorer's address bar often give) inside plain
double quotes would turn the closing quote into a literal one and swallow the rest of the command line.

The rule of the Microsoft C runtime (and `CommandLineToArgvW`, which Python uses): backslashes are literal unless they
directly precede a double quote, where 2n backslashes mean n and an odd count escapes the quote. So the backslashes at
the end of the value are doubled before the closing quote. A double quote cannot be part of a Windows path, so quotes
in the value are dropped.

The Pascal function is a line-by-line port of this one, and a test runs this one through the real parser (on Windows)
so the rule it encodes is checked, not assumed.
"""

from __future__ import annotations


def quote_arg(value: str) -> str:
    text = value.replace('"', "")
    trailing = len(text) - len(text.rstrip("\\"))
    return f'"{text}{chr(92) * trailing}"'
