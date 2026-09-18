"""Action model.

An :class:`Action` is a declarative description of a change: what to run, why,
how risky it is, and — crucially — how to undo it.  Nothing here executes
anything.  The executor does that.

The legacy script had no such concept.  It interleaved "decide" and "do" inside
nested functions, which is why it could not offer a dry run, could not report
what it had changed, and could not undo any of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import Impact, Risk


@dataclass(frozen=True)
class Command:
    """One device-side command, with its inverse where one exists."""

    shell: str
    label: str
    inverse: str | None = None
    target: str = ""

    @property
    def reversible(self) -> bool:
        return self.inverse is not None


@dataclass
class Action:
    """A single logical change to the device."""

    id: str
    title: str
    category: str
    risk: Risk
    commands: list[Command] = field(default_factory=list)
    impact: Impact = Impact.MINOR
    rationale: str = ""
    #: Groups that must not be split across parallel workers (rarely needed).
    serial_group: str = ""

    @property
    def reversible(self) -> bool:
        return all(c.reversible for c in self.commands)

    @property
    def targets(self) -> list[str]:
        return [c.target for c in self.commands if c.target]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "risk": self.risk.value,
            "impact": self.impact.value,
            "rationale": self.rationale,
            "commands": [c.shell for c in self.commands],
            "reversible": self.reversible,
        }


class ActionBuilder:
    """Interface for anything that turns a snapshot into actions."""

    category = "generic"

    def build(self, snapshot) -> list[Action]:  # pragma: no cover - interface
        raise NotImplementedError
