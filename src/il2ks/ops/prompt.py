"""Asking the admin questions in the terminal. A `Prompter` is injected so tests can script the answers."""

from __future__ import annotations

import getpass
import sys
from typing import Protocol


class Cancelled(Exception):
    """The admin closed the input (Ctrl+Z / Ctrl+D) or answered nothing where an answer is required."""


class Prompter(Protocol):
    def say(self, text: str = "") -> None: ...

    def ask(self, question: str, default: str = "") -> str:
        """The typed answer; Enter alone gives `default`."""
        ...

    def ask_secret(self, question: str) -> str:
        """An answer that is not shown while typing."""
        ...

    def confirm(self, question: str, default: bool) -> bool: ...


class ConsolePrompter:
    """The real thing: `input()` and `getpass`."""

    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"{question}{suffix}: ").strip()
        except EOFError:
            raise Cancelled from None
        return answer or default

    def ask_secret(self, question: str) -> str:
        try:
            return getpass.getpass(f"{question}: ")
        except EOFError:
            raise Cancelled from None

    def confirm(self, question: str, default: bool) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            answer = self.ask(f"{question} ({hint})").lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.say("Please answer y or n.")


def interactive_terminal() -> bool:
    """True when a person can answer questions: both input and output are a terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()
