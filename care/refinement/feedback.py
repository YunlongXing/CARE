"""Validation feedback models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepairFeedback:
    """Feedback passed into a future repair prompt."""

    messages: list[str] = field(default_factory=list)

    @classmethod
    def from_messages(cls, messages: list[str]) -> "RepairFeedback":
        return cls(messages=messages)
