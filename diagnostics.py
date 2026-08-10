"""Diagnostic model shared by the linter, the mapper and the validator."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional


class Severity(enum.IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Diagnostic:
    rule: str
    severity: Severity
    message: str
    subject: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity.label,
            "subject": self.subject,
            "message": self.message,
        }

    def __str__(self) -> str:
        subject = f" [{self.subject}]" if self.subject else ""
        return f"{self.severity.label:<7} {self.rule:<22}{subject} {self.message}"


class DiagnosticBag:
    """Collects diagnostics and applies the ignore / strict / fail-on policy."""

    def __init__(self, ignore: Iterable[str] = (), strict: bool = False) -> None:
        self._items: list[Diagnostic] = []
        self._ignored: list[Diagnostic] = []
        self.ignore = {rule.upper() for rule in ignore}
        self.strict = strict

    def add(self, rule: str, severity: Severity, message: str, subject: Optional[str] = None) -> None:
        diag = Diagnostic(rule, severity, message, subject)
        if rule.upper() in self.ignore:
            self._ignored.append(diag)
            return
        if self.strict and severity is Severity.WARNING:
            diag = Diagnostic(rule, Severity.ERROR, message, subject)
        self._items.append(diag)

    def error(self, rule: str, message: str, subject: Optional[str] = None) -> None:
        self.add(rule, Severity.ERROR, message, subject)

    def warning(self, rule: str, message: str, subject: Optional[str] = None) -> None:
        self.add(rule, Severity.WARNING, message, subject)

    def info(self, rule: str, message: str, subject: Optional[str] = None) -> None:
        self.add(rule, Severity.INFO, message, subject)

    def extend(self, other: "DiagnosticBag") -> None:
        self._items.extend(other._items)
        self._ignored.extend(other._ignored)

    def of(self, severity: Severity) -> list[Diagnostic]:
        return [d for d in self._items if d.severity is severity]

    @property
    def errors(self) -> list[Diagnostic]:
        return self.of(Severity.ERROR)

    @property
    def warnings(self) -> list[Diagnostic]:
        return self.of(Severity.WARNING)

    @property
    def ignored(self) -> list[Diagnostic]:
        return list(self._ignored)

    def has_errors(self) -> bool:
        return bool(self.errors)

    def rules_hit(self) -> set[str]:
        return {d.rule for d in self._items}

    def counts(self) -> dict[str, int]:
        return {
            "error": len(self.of(Severity.ERROR)),
            "warning": len(self.of(Severity.WARNING)),
            "info": len(self.of(Severity.INFO)),
            "ignored": len(self._ignored),
        }

    def to_list(self) -> list[dict]:
        return [d.to_dict() for d in self._items]

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
