# l2-tca — L2 order book reconstruction and execution cost analysis

Real-time reconstruction of a venue's L2 order book from its WebSocket feed, with
deterministic replay, columnar storage and a latency harness around the hot path.

Stage 1 covers one venue (Kraken public WebSocket v2, `book` channel) and one
configurable instrument (`BTC/USD`, depth 100).

## The shape of this repository

The infrastructure is complete and tested. The analytical core is **specified but
not implemented**: the order book, the microstructure factors and the cost model are
written by hand, and their contracts live in the module docstrings and in an
executable acceptance suite.

| Component | State |
|---|---|
| `src/l2tca/feed/` — WS client, recorder, replay, frame decoding, mock venue | complete |
| `src/l2tca/io/` — Arrow schemas, hour-partitioned Parquet, read-back validation | complete |
| `src/l2tca/bench/` — latency sampling and the per-message benchmark harness | complete |
| `src/l2tca/cli.py` — `record` / `replay` / `export` / `inspect` / `bench` / `mock-server` | complete |
| `src/l2tca/book/` — order book replica, fixed-depth trim, CRC32 checksum | **specified stub** |
| `src/l2tca/signals/` — microprice, imbalance, spread, book slope | **specified stub** |
| `src/l2tca/tca/` — book walk, slippage, implementation shortfall, cost curve | **specified stub** |

`tests/test_core_contract.py` holds 29 tests that skip while the core is a stub and
start running the moment it is implemented — the specification, executable.

## Quick start

```bash
uv sync

# Record two minutes from the bundled mock venue — no network needed.
uv run l2tca record --mock --duration 120 --out data/raw

# Or use the committed sample and skip straight to the interesting parts.
uv run l2tca replay  data/samples/mock_btcusd_book_2min.jsonl.gz --speed 10 --show 3
uv run l2tca export  data/samples/mock_btcusd_book_2min.jsonl.gz --out data/parquet
uv run l2tca inspect --root data/parquet
uv run l2tca bench   data/samples/mock_btcusd_book_2min.jsonl.gz

uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

### Capturing live data

```bash
uv run l2tca record --symbol BTC/USD --depth 100 --duration 600   # ten minutes
```

Kraken v2 uses `BTC/USD`, not the legacy `XBT/USD`; valid depths are 10, 25, 100,
500 and 1000. `data/raw/` is gitignored.

> The environment this project was scaffolded in could not reach the venue: the
> egress policy answered `403` to `CONNECT ws.kraken.com:443`. The feed reports that
> as a configuration error rather than retrying it forever, and the committed sample
> is synthetic — see [`data/samples/README.md`](data/samples/README.md).

## How the pieces fit

```
                  ┌──────────────┐
  Kraken v2 WS ──▶ │  feed/       │ ── RawMessage(seq, session, recv_ns, recv_wall_ns, payload)
                  │  kraken.py   │        │
                  └──────────────┘        │
                         ▲                ├─▶ feed/recorder.py ─▶ data/raw/*.jsonl   (source of truth)
                         │                │                              │
                  feed/mock_server.py     │                              ▼
                  (offline / CI)          │                       feed/replay.py
                                          │                              │
                                          ▼                              ▼
                                   feed/parser.py ──────────────▶  book/  (stub)
                                          │                              │
                                          │                        signals/, tca/  (stub)
                                          ▼                              │
                                    io/convert.py ─────────────────────┐  │
                                          │                            ▼  ▼
                                          └──────────────▶ io/writer.py ─▶ data/parquet/{tick,snapshot,signal}
                                                                          │
                                                            io/reader.py ─┴─▶ Polars + validation
```

Every stage after the recorder reads from a file, so the whole system is
developable, testable and profilable offline.

## Design decisions

**Two clocks on every message.** `recv_ns` is `perf_counter_ns()` — monotonic, high
resolution, meaningless outside the recording process. `recv_wall_ns` is
`time_ns()` — comparable across reconnects and restarts. Latency measurement uses
the first; hour partitioning and replay pacing use the second. Conflating them is
how a replay of a multi-session capture ends up sleeping for negative durations.

**The raw payload is stored untouched.** The recorder never re-serialises a frame.
A decoder bug is then fixable by re-running the decoder over yesterday's file rather
than by capturing a fresh day, and the venue's original decimal strings — which the
CRC32 checksum is defined over — stay recoverable.

**JSONL is the source of truth; Parquet is derived.** A Parquet file is only
readable once its footer is written on close, so a process killed mid-capture leaves
nothing behind. A line-buffered JSONL file loses at most the line being written.
Parquet is a rebuildable artefact of that record, not the record.

**The read path stays parse-free.** The client decodes frames only until the venue
confirms the subscription, then stops: in the steady state, receiving a message
costs a socket read and two clock reads. `permessage-deflate` is disabled for the
same reason — it adds CPU and jitter to the very latency being measured.

**Not all failures are transient.** A dropped socket, a stale connection and a
refused TCP connect are retried on an exponential backoff with **full jitter** (an
undelayed herd reconnecting in lockstep is how a venue-side blip becomes an outage).
A 4xx handshake rejection or a refused subscription is not retried at all: the venue
has already answered, and hammering it just earns a rate limit.

**Storage validates itself.** `l2tca inspect` reads the tables back with Polars and
asserts columns, schema version, non-null constraints, per-session monotonicity, and
that every row sits in the hour partition its own timestamp implies. Writing data is
half a storage layer; reading it back is the half that catches bugs.

## Benchmarking

```
$ uv run l2tca bench data/samples/mock_btcusd_book_2min.jsonl.gz
noop:       2155 msgs in  1.0ms (2,243,752 msg/s)  p50=0.11us  p99=0.12us
parse-only: 2155 msgs in 15.0ms (  143,680 msg/s)  p50=5.55us  p99=19.99us
decode:     2155 msgs in 28.7ms (   75,002 msg/s)  p50=10.62us p99=55.16us
book:       SKIPPED — not implemented yet
```

Four targets over the same recorded messages: the harness floor (two clock reads),
`json.loads` alone, full typed decoding, and the book on top. Reading a book
implementation against those floors is more informative than any absolute number,
especially on a shared or virtualised machine. Percentiles are nearest-rank, the
first `--warmup` messages are applied but excluded, and cyclic GC is paused for the
measured window by default (`--gc` leaves it on — the honest reading is both).

## Implementing the core

Read, in order:

1. `src/l2tca/book/orderbook.py` — delta semantics, the fixed-depth trim, the
   checksum algorithm, and the data-structure trade-offs.
2. `src/l2tca/signals/microstructure.py` — factor definitions and why `None` is not
   `0.0`.
3. `src/l2tca/tca/cost.py` — the sign convention, reference prices, and what a
   book walk cannot tell you about market impact.

Then work test-first:

```bash
uv run pytest tests/test_core_contract.py -v   # 29 skips become 29 tests
uv run l2tca bench data/samples/mock_btcusd_book_2min.jsonl.gz
```

## Scope

Stage 1 is deliberately one venue, one symbol, no live trading, no web front end and
no database. Out of scope by design: order routing, multi-venue consolidation, L3 /
per-order books (Kraken's public feed is L2 only), and hidden liquidity — a book
walk measures the mechanical cost of consuming *visible* size and nothing more.
