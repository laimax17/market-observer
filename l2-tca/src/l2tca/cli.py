"""Command line entry point.

l2tca record   --duration 600            # capture a live session to JSONL
l2tca record   --mock --duration 60      # same, against the bundled mock venue
l2tca replay   data/raw/... --speed 10   # play a recording back
l2tca export   data/raw/... --out data/parquet
l2tca inspect  --root data/parquet --table tick
l2tca bench    data/raw/... --limit 50000
l2tca mock-server                        # a local stand-in for Kraken v2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from l2tca import __version__
from l2tca.bench.harness import run_suite
from l2tca.config import VALID_DEPTHS, Settings
from l2tca.feed.kraken import ConnectionRefused, KrakenFeed, SubscriptionRejected
from l2tca.feed.mock_server import MockKrakenServer, serve_forever
from l2tca.feed.parser import BookFrame, FrameDecodeError, parse_frame
from l2tca.feed.recorder import JsonlRecorder, record_stream
from l2tca.feed.replay import JsonlReplay
from l2tca.io.convert import export_to_parquet
from l2tca.io.reader import validate_table
from l2tca.io.schema import TABLES

log = logging.getLogger("l2tca")


# --------------------------------------------------------------------- record


async def _record(args: argparse.Namespace) -> int:
    settings = Settings(symbol=args.symbol, depth=args.depth, raw_dir=args.out)
    if args.url:
        settings.ws_url = args.url

    async with contextlib.AsyncExitStack() as stack:
        prefix = f"kraken_{settings.file_symbol}"
        if args.mock:
            server = await stack.enter_async_context(
                MockKrakenServer(
                    symbol=settings.symbol,
                    depth=settings.depth,
                    updates_per_second=args.mock_rate,
                    seed=args.mock_seed,
                    drop_after_messages=args.mock_drop_after,
                )
            )
            settings.ws_url = server.url
            prefix = f"mock_{settings.file_symbol}"
            log.info("recording from the bundled mock venue at %s", server.url)

        feed = KrakenFeed(settings)
        recorder = JsonlRecorder(settings.raw_dir, prefix=prefix)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, feed.request_stop)

        deadline: asyncio.TimerHandle | None = None
        if args.duration:
            deadline = loop.call_later(args.duration, feed.request_stop)

        started = time.perf_counter()
        last_report = started

        def progress(_message: object) -> None:
            nonlocal last_report
            now = time.perf_counter()
            if now - last_report >= 5.0:
                last_report = now
                print(
                    f"  {recorder.written:>8} messages  {now - started:6.1f}s elapsed",
                    file=sys.stderr,
                )

        try:
            count = await record_stream(
                feed.stream(),
                recorder,
                max_messages=args.max_messages,
                on_message=progress,
            )
        finally:
            if deadline is not None:
                deadline.cancel()

    elapsed = time.perf_counter() - started
    print(f"recorded {count} messages in {elapsed:.1f}s ({count / max(elapsed, 1e-9):.1f} msg/s)")
    for path in recorder.paths:
        print(f"  {path}  ({path.stat().st_size / 1e6:.2f} MB)")
    return 0 if count else 1


# --------------------------------------------------------------------- replay


async def _replay(args: argparse.Namespace) -> int:
    replay = JsonlReplay(args.path, speed=args.speed, limit=args.limit, strict=not args.lenient)
    counts: dict[str, int] = {}
    first_wall = last_wall = None
    shown = 0
    started = time.perf_counter()

    async for message in replay.stream():
        first_wall = first_wall if first_wall is not None else message.recv_wall_ns
        last_wall = message.recv_wall_ns
        try:
            frame = parse_frame(message.payload)
            name = type(frame).__name__
            if isinstance(frame, BookFrame):
                name += " (snapshot)" if frame.is_snapshot else " (update)"
        except FrameDecodeError as exc:
            name = f"undecodable: {exc}"
        counts[name] = counts.get(name, 0) + 1
        if args.show and shown < args.show:
            shown += 1
            print(f"seq={message.seq} session={message.session} {name}: {message.payload[:160]}")

    elapsed = time.perf_counter() - started
    total = sum(counts.values())
    recorded_s = (last_wall - first_wall) / 1e9 if first_wall and last_wall else 0.0
    print(f"replayed {total} messages spanning {recorded_s:.1f}s of market time in {elapsed:.1f}s")
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>8}  {name}")
    if replay.skipped:
        print(f"  skipped {replay.skipped} malformed records")
    return 0


# --------------------------------------------------------------- export/inspect


def _export(args: argparse.Namespace) -> int:
    replay = JsonlReplay(args.path, limit=args.limit, strict=not args.lenient)
    report = export_to_parquet(replay.iter_messages(), args.out, row_group_size=args.row_group_size)
    print(report.describe())
    for path in report.files:
        print(f"  {path}  ({path.stat().st_size / 1e6:.2f} MB)")

    failures = 0
    for table in ("tick", "snapshot"):
        validation = validate_table(args.out, table)
        print(validation.describe())
        failures += 0 if validation.ok else 1
    return 1 if failures else 0


def _inspect(args: argparse.Namespace) -> int:
    tables = [args.table] if args.table else list(TABLES)
    failures = 0
    for table in tables:
        try:
            report = validate_table(args.root, table)
        except FileNotFoundError as exc:
            print(f"{table}: {exc}")
            continue
        print(report.describe())
        print()
        failures += 0 if report.ok else 1
    return 1 if failures else 0


# ---------------------------------------------------------------------- bench


def _bench(args: argparse.Namespace) -> int:
    replay = JsonlReplay(args.path, limit=args.limit, strict=not args.lenient)
    messages = list(replay.iter_messages())
    if not messages:
        print("no messages to benchmark")
        return 1
    print(f"benchmarking {len(messages)} recorded messages (warmup={args.warmup})")
    suite = run_suite(
        messages,
        warmup=args.warmup,
        gc_disabled=not args.gc,
    )
    print(suite.describe())
    return 0


# ----------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l2tca", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"l2tca {__version__}")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: INFO)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="capture a live session to JSONL")
    record.add_argument("--symbol", default=Settings().symbol, help="venue symbol, e.g. BTC/USD")
    record.add_argument("--depth", type=int, default=100, choices=VALID_DEPTHS)
    record.add_argument("--out", type=Path, default=Path("data/raw"), help="output directory")
    record.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    record.add_argument("--max-messages", type=int, default=None, help="stop after N messages")
    record.add_argument("--url", default=None, help="override the WebSocket endpoint")
    record.add_argument(
        "--mock", action="store_true", help="record from the bundled mock venue (offline)"
    )
    record.add_argument("--mock-rate", type=float, default=25.0, help="mock updates per second")
    record.add_argument("--mock-seed", type=int, default=7, help="mock random seed")
    record.add_argument(
        "--mock-drop-after",
        type=int,
        default=None,
        help="make the mock venue drop each connection after N frames, to exercise reconnect",
    )
    record.set_defaults(func=lambda args: asyncio.run(_record(args)))

    replay = sub.add_parser("replay", help="play a recording back at the recorded cadence")
    replay.add_argument("path", type=Path, help="a .jsonl file or a directory of them")
    replay.add_argument(
        "--speed", type=float, default=1.0, help="time compression; 0 = as fast as possible"
    )
    replay.add_argument("--limit", type=int, default=None, help="stop after N messages")
    replay.add_argument("--show", type=int, default=0, help="print the first N decoded frames")
    replay.add_argument("--lenient", action="store_true", help="skip malformed records")
    replay.set_defaults(func=lambda args: asyncio.run(_replay(args)))

    export = sub.add_parser("export", help="convert a recording into the Parquet tables")
    export.add_argument("path", type=Path, help="a .jsonl file or a directory of them")
    export.add_argument("--out", type=Path, default=Path("data/parquet"))
    export.add_argument("--limit", type=int, default=None)
    export.add_argument("--row-group-size", type=int, default=50_000)
    export.add_argument("--lenient", action="store_true", help="skip malformed records")
    export.set_defaults(func=_export)

    inspect = sub.add_parser("inspect", help="read the Parquet tables back and validate them")
    inspect.add_argument("--root", type=Path, default=Path("data/parquet"))
    inspect.add_argument("--table", choices=sorted(TABLES), default=None)
    inspect.set_defaults(func=_inspect)

    bench = sub.add_parser("bench", help="latency profile of the per-message hot path")
    bench.add_argument("path", type=Path, help="a .jsonl file or a directory of them")
    bench.add_argument("--limit", type=int, default=None)
    bench.add_argument("--warmup", type=int, default=1_000)
    bench.add_argument("--gc", action="store_true", help="leave cyclic GC enabled while measuring")
    bench.add_argument("--lenient", action="store_true", help="skip malformed records")
    bench.set_defaults(func=_bench)

    mock = sub.add_parser("mock-server", help="serve a local stand-in for Kraken v2")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=8765)
    mock.add_argument("--symbol", default=Settings().symbol)
    mock.add_argument("--depth", type=int, default=100, choices=VALID_DEPTHS)
    mock.add_argument("--rate", type=float, default=25.0, help="updates per second")
    mock.set_defaults(
        func=lambda args: asyncio.run(
            serve_forever(
                host=args.host,
                port=args.port,
                symbol=args.symbol,
                depth=args.depth,
                updates_per_second=args.rate,
            )
        )
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (ConnectionRefused, SubscriptionRejected) as exc:
        # A refused handshake or a rejected subscription is a configuration
        # problem, not a crash: say what happened, skip the traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
