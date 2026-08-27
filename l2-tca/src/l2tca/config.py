"""Runtime configuration.

Every value has a working default, can be overridden by an ``L2TCA_*`` environment
variable, and can be overridden again by a CLI flag. Keeping the defaults in one
dataclass means a test can build a ``Settings`` object explicitly instead of
depending on process environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Kraken's public WebSocket v2 endpoint. v2 carries the ``book`` channel used here.
DEFAULT_WS_URL = "wss://ws.kraken.com/v2"

#: Kraken v2 renamed the legacy ``XBT/USD`` pair to ``BTC/USD``; v2 rejects ``XBT/USD``.
DEFAULT_SYMBOL = "BTC/USD"

#: Kraken only accepts these book depths.
VALID_DEPTHS = (10, 25, 100, 500, 1000)


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass(slots=True)
class Settings:
    """Everything the feed, recorder and writers need to run."""

    ws_url: str = field(default_factory=lambda: _env_str("L2TCA_WS_URL", DEFAULT_WS_URL))
    symbol: str = field(default_factory=lambda: _env_str("L2TCA_SYMBOL", DEFAULT_SYMBOL))
    depth: int = field(default_factory=lambda: _env_int("L2TCA_DEPTH", 100))

    #: Reconnect if no frame at all arrives within this many seconds. Kraken emits a
    #: heartbeat every second on an idle subscription, so silence means a dead socket.
    stale_after_s: float = field(default_factory=lambda: _env_float("L2TCA_STALE_AFTER_S", 10.0))
    #: websockets' own protocol-level keepalive.
    ping_interval_s: float = field(
        default_factory=lambda: _env_float("L2TCA_PING_INTERVAL_S", 20.0)
    )
    ping_timeout_s: float = field(default_factory=lambda: _env_float("L2TCA_PING_TIMEOUT_S", 10.0))

    backoff_initial_s: float = field(
        default_factory=lambda: _env_float("L2TCA_BACKOFF_INITIAL_S", 0.5)
    )
    backoff_max_s: float = field(default_factory=lambda: _env_float("L2TCA_BACKOFF_MAX_S", 30.0))
    #: A connection that survives this long is considered healthy: reset the backoff.
    backoff_reset_after_s: float = field(
        default_factory=lambda: _env_float("L2TCA_BACKOFF_RESET_AFTER_S", 60.0)
    )

    raw_dir: Path = field(default_factory=lambda: Path(_env_str("L2TCA_RAW_DIR", "data/raw")))
    parquet_dir: Path = field(
        default_factory=lambda: Path(_env_str("L2TCA_PARQUET_DIR", "data/parquet"))
    )

    def __post_init__(self) -> None:
        if self.depth not in VALID_DEPTHS:
            raise ValueError(f"depth must be one of {VALID_DEPTHS}, got {self.depth}")
        self.raw_dir = Path(self.raw_dir)
        self.parquet_dir = Path(self.parquet_dir)

    @property
    def file_symbol(self) -> str:
        """Filesystem-safe form of the symbol, e.g. ``BTC/USD`` -> ``btcusd``."""
        return self.symbol.replace("/", "").replace("-", "").lower()
