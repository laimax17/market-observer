# Kraken WebSocket v2 — field notes

Working notes on the parts of the protocol this project depends on. The
authoritative reference is <https://docs.kraken.com/websockets-v2/>; what follows is
the subset that matters here, plus the details that cost time to discover.

## Endpoint and subscription

```
wss://ws.kraken.com/v2
```

```json
{"method": "subscribe",
 "params": {"channel": "book", "symbol": ["BTC/USD"], "depth": 100, "snapshot": true},
 "req_id": 1}
```

* v2 uses **`BTC/USD`**. The legacy `XBT/USD` spelling belongs to v1 and is rejected.
* `depth` must be one of **10, 25, 100, 500, 1000**.
* The ack echoes the accepted parameters and carries `success`. A rejection
  (`success: false`) is final — retrying the identical request only earns a rate
  limit.

## Frames seen on a book subscription

| Frame | Shape | Notes |
|---|---|---|
| status | `{"channel":"status","type":"update","data":[{...}]}` | first frame after connect |
| subscribe ack | `{"method":"subscribe","success":true,"result":{...}}` | carries `req_id` |
| snapshot | `{"channel":"book","type":"snapshot","data":[{...}]}` | full image, no `timestamp` |
| update | `{"channel":"book","type":"update","data":[{...}]}` | deltas, carries `timestamp` |
| heartbeat | `{"channel":"heartbeat"}` | roughly once a second when idle |

`data` is a list with one entry per symbol; a single-symbol subscription always
yields exactly one entry.

## Delta semantics

* `qty` is the **new absolute size** at that price, never an increment.
* `qty == 0` deletes the level.
* The book is **fixed depth**. An insertion inside the top `depth` pushes the far
  level off the end, and the venue does *not* send a delete for it — the replica has
  to trim its own tail. Skipping that is the classic slow-drift bug, and the
  checksum is what catches it.
* A snapshot replaces everything. After a reconnect the venue sends a fresh one, and
  any state carried across the gap is stale.

## Checksum

A CRC32 over the top of book, published on every book frame:

1. Top 10 asks ascending, then top 10 bids descending.
2. Format each price and quantity at the pair's precision from
   `/0/public/AssetPairs` (`pair_decimals` and `lot_decimals`).
3. Remove the decimal point, strip leading zeros.
4. Concatenate everything into one string.
5. `zlib.crc32(text.encode())` — compare as an unsigned 32-bit integer.

The checksum is defined over the decimal digits the venue sent, so a replica holding
`float` prices has to reproduce that formatting exactly. Scaled integer prices
(`round(price * 10**pair_decimals)`) make this and level equality both simpler.

A mismatch is not locally repairable: resubscribe, rebuild from the fresh snapshot,
and count the event. A rising mismatch rate is a signal about the feed handler.

## Timestamps

`timestamp` is RFC 3339 with microseconds and a `Z` suffix. Before Python 3.12,
`datetime.fromisoformat` is picky about both, which is why the parser normalises them
itself. Convert to nanoseconds with integer arithmetic: `int(dt.timestamp() * 1e9)`
rounds to roughly 256ns, because a float64 mantissa cannot hold nanoseconds since
1970.

Treat the venue timestamp as informational. It is the venue's clock, subject to its
own queueing, and is absent on snapshots — `recv_wall_ns` is the axis that is always
there.

## Keepalive and liveness

The venue answers protocol-level pings and also accepts `{"method":"ping"}`. Neither
is the primary liveness signal here: the heartbeat channel means an idle
subscription still produces a frame every second, so **silence is the failure
signal**. This client reconnects if nothing at all arrives within
`L2TCA_STALE_AFTER_S` (10s by default), which catches a half-open socket long before
TCP does.

Cap the closing handshake (`close_timeout`) as well: the default 10s means a socket
that has already gone silent also delays the reconnect meant to replace it.
