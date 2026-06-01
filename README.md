# market-observer

A read-only market observation agent. Produces a daily pre-market briefing
for a watchlist of 10 symbols, combining:

- Code-computed technical indicators (RSI, MACD, moving averages, etc.)
- End-of-day options signals (IV term structure, put/call ratio, IV skew)
- A multi-agent LLM panel (technical / options / macro analysts + an editor)
  that writes a narrative interpreting the structured facts

Pushes the briefing to Discord via webhook. Does **not** place orders.
Does **not** output a direction + confidence number. Does **not** touch any
real trading system.

## Design

See [`docs/00_design.md`](docs/00_design.md) for the authoritative design:
architecture, the multi-agent DAG, data sources, and the T-01..T-11 roadmap.

## Status

Implementing. Following the roadmap in `docs/00_design.md` §9.

## Relationship to `agentic_trading_system`

This is an intentionally separate sandbox project. The main
`agentic_trading_system` is a fail-closed, single-writer, event-sourced
trading core with frozen contracts; this project is exploratory,
read-only, and may evolve quickly. Code is **not** shared between them.

If signals here prove valuable, the path to integration is via the main
system's `04_intelligence_layer` design — not by linking code directly.
