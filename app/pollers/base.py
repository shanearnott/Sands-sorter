from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True)
class RawDoc:
    data: bytes
    filename: str
    source_msg_ref: str
    source_id: int
    sender: str | None = None


class Poller(Protocol):
    """Yields newly observed documents from a source. Implementations are responsible
    for advancing the source cursor only after successful hand-off."""

    def fetch(self) -> Iterable[RawDoc]: ...
