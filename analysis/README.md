# analysis/

Measurement tools. Nothing here runs as part of the service, and nothing here
writes to its database — these scripts read, compare and print.

They exist because two questions about this service can only be answered with
numbers, and both were once answered with estimates that turned out wrong.

## `compare_with_intraday.py`

Compares the candles this service produced against EODHD's Intraday Historical
API, which carries the full consolidated tape.

The reason this matters is in [ARCHITECTURE.md](../ARCHITECTURE.md) §4.4: the
WebSocket feed is **Cboe EDGX, a single exchange**, so an absent candle usually
does not mean nobody traded, and volume is a fraction of market volume. This
script measures both, per ticker.

```bash
# every tracked ticker, main session
python analysis/compare_with_intraday.py 2026-09-03

# a few tickers, extended hours, saving a table
python analysis/compare_with_intraday.py 2026-09-03 \
    --tickers NVDA,VPG,ALAB --session extended --csv out.csv
```

Reads `EODHD_API_KEY`, `API_URL` and `API_KEY` from the environment or `.env`.
Intraday only has yesterday and earlier — asking for today returns nothing.

**It refuses to run unless the service is aggregating at 1 minute**, because the
reference bars are 1-minute and comparing them against 5-minute candles would
produce confident nonsense.

Per ticker it reports the share of market volume that reached the service, how
many session minutes produced a candle here against the full tape, and how many
of the minutes with no candle traded on another venue. It ends with the totals
and — worth watching — any ticker that produced **no candle at all** while the
tape shows it trading. That is the signature of a subscription EODHD accepted
and never streamed; cross-check it against `subscription_health` in
`GET /status`.

### What the numbers looked like when this was written

51 tickers, main session, 3 September 2026:

| | |
| --- | --- |
| Volume reaching the service | 2.8% (0.7–5.6% per ticker, median 2.8%) |
| Minutes with no candle that traded elsewhere | 88.7% |
| Tickers producing nothing at all | 2 — SPCX and DASH |

The volume share is near-constant across tickers because it is a property of the
feed, not of the stock. Whether that costs whole minutes depends on liquidity: an
active name prints in every minute even on one venue, a thin one does not. On
that day 14 of 51 tickers had all 390 minutes, and 5 lost more than half.

Re-run it after a session to confirm those figures still hold.

## A note on `empty_interval_audit.jsonl`

Version 0.9.11 added an optional task that recorded which empty intervals *could*
have been filled with a zero-volume candle, writing none. It answered its
question — filling them would have been wrong — and is off by default; see
[docs/EMPTY_INTERVAL_AUDIT.md](../docs/EMPTY_INTERVAL_AUDIT.md) if it is ever
turned back on.

Files written by **0.9.11 specifically** undercount: the chain rule was asked of
the engine, which only advances when a candle is actually written, so a run of
silent intervals counted as one. Files from 0.9.12 onward are correct.
