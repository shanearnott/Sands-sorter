from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RawDoc:
    """One document discovered by a poller, ready for the shared pipeline.

    The poller fills in everything except the OCR/classification — that
    happens downstream in `app.pipeline.process_raw_doc`.
    """

    data: bytes
    filename: str
    source_msg_ref: str          # Gmail msg id, Dropbox path, eml filename, etc.
    source_id: int
    mime_type: str = "application/octet-stream"
    sender: str | None = None
    received_at: datetime | None = None
    extra: dict = field(default_factory=dict)  # poller-specific bag (e.g. "body_only": True)


class Poller(Protocol):
    """Yields newly-observed documents and acknowledges them once handled."""

    def fetch(self) -> Iterable[RawDoc]: ...

    def mark_processed(self, raw: RawDoc) -> None:
        """Called after the pipeline has filed `raw` (or routed it to Unsorted).

        Implementations should advance whatever cursor / move-folder state they
        track so the same item isn't re-emitted on the next poll.
        """
