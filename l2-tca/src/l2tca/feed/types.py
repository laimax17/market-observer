"""The wire-level record produced by the feed and persisted by the recorder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Bumped whenever the JSONL record layout changes incompatibly.
RECORD_VERSION = 1


@dataclass(frozen=True, slots=True)
class RawMessage:
    """One WebSocket text frame, exactly as it came off the wire, plus receive stamps.

    Two clocks are captured on purpose, because they answer different questions:

    ``recv_ns``
        ``time.perf_counter_ns()``. Monotonic, high resolution, *not* comparable
        across processes or across a machine sleeping. This is the clock used for
        every latency measurement inside one process (feed -> book -> signal).
    ``recv_wall_ns``
        ``time.time_ns()``. Comparable across processes and across a restart, so it
        is the clock used for hour partitioning and for pacing a replay of a file
        that may span several recording sessions.

    ``session`` is the connection generation: it starts at 0 and increments on every
    successful reconnect. Sequence numbers restart per session, and ``recv_ns`` is
    only comparable within a session, so downstream code must group by it.
    """

    seq: int
    session: int
    recv_ns: int
    recv_wall_ns: int
    payload: str

    def to_json(self) -> str:
        """Serialise to a single JSONL line (no trailing newline).

        The payload is stored as an opaque string rather than a nested object: the
        recording stays byte-for-byte lossless, and recording never pays for a JSON
        parse it does not need.
        """
        return json.dumps(
            {
                "v": RECORD_VERSION,
                "seq": self.seq,
                "session": self.session,
                "recv_ns": self.recv_ns,
                "recv_wall_ns": self.recv_wall_ns,
                "payload": self.payload,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line: str) -> RawMessage:
        """Parse one JSONL line written by :meth:`to_json`."""
        obj: dict[str, Any] = json.loads(line)
        version = obj.get("v", RECORD_VERSION)
        if version != RECORD_VERSION:
            raise ValueError(
                f"unsupported raw record version {version!r} (this build reads v{RECORD_VERSION})"
            )
        return cls(
            seq=int(obj["seq"]),
            session=int(obj["session"]),
            recv_ns=int(obj["recv_ns"]),
            recv_wall_ns=int(obj["recv_wall_ns"]),
            payload=obj["payload"],
        )
