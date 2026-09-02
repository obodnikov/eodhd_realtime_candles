# Empty-interval audit

A measurement task that answers one question with numbers instead of estimates:

> If the service wrote a zero-volume candle for every interval in which nothing traded,
> how many candles would that be, for which tickers, and how often would the
> preconditions actually hold?

**It writes no candles.** It reads engine state, judges each empty interval against the
same conditions a real fill would have to satisfy, and appends the verdict to a file.
Nothing in the `candles` table changes, and the rule "never fabricate candle values" in
[AI_WEBSOCKET_ENGINE.md](../AI_WEBSOCKET_ENGINE.md) is not touched.

## Why it exists

An interval can produce no candle for two different reasons, and until v0.9.10 they were
indistinguishable:

- **The interval had trades**, but the candle sat in memory waiting for the ticker's next
  trade. Fixed in v0.9.10 — candles now close on a timer.
- **The interval had no trades at all.** No candle exists, and none ever will.

Only the second case is left. Whether it is worth filling depends on how often it happens
and to which tickers, and that has never been measured against the current code. Earlier
estimates were taken before v0.9.10 and mixed both causes together.

## Turning it on

```bash
EMPTY_INTERVAL_AUDIT=extended            # off | regular | extended
EMPTY_INTERVAL_AUDIT_PATH=/data/empty_interval_audit.jsonl   # optional
```

`regular` is 09:30–16:00 ET, `extended` is 04:00–20:00 ET; weekends are excluded. Both are
evaluated in New York local time, so daylight saving is handled. Holidays need no calendar:
on a holiday the chain never starts, so every interval fails on its own.

The default is `off`, and the task is not started at all in that case. Both variables are
environment-only — the task runs in the WebSocket worker, so `PATCH /config` refuses them
and says which variable to set.

Restart the WebSocket worker after changing them.

## What it records

One line of JSON per ticker per empty interval, appended to the file. Intervals that
produced a candle are not recorded — there is nothing to measure about them.

```json
{
  "ticker": "ALAB",
  "bucket": 1788340620,
  "bucket_utc": "2026-09-02T09:17:00+00:00",
  "interval_minutes": 1,
  "would_fill": true,
  "engine_reason": "would_fill",
  "feed_steady": true,
  "subscribed_throughout": true,
  "inside_session": true,
  "price": 153.5,
  "observed_at": "2026-09-02T09:17:03.412870+00:00"
}
```

`would_fill` is the answer: true only when every condition below held.

| Field | Judged by | Meaning |
| --- | --- | --- |
| `engine_reason` | engine | `would_fill`, or why not — see below |
| `feed_steady` | worker | the feed was connected at both ends of the interval with an unchanged connection count |
| `subscribed_throughout` | worker | the ticker was subscribed at both ends |
| `inside_session` | worker | the interval starts inside the configured window |
| `price` | engine | the previous candle's close — what a synthetic candle would carry in all four price fields |

`engine_reason` values:

| Value | Meaning |
| --- | --- |
| `would_fill` | the chain is intact and no candle exists for this interval |
| `no_previous_candle` | this ticker has produced no candle yet, so there is no chain and no known price |
| `chain_broken` | the immediately preceding interval produced no candle either — after a hole of unknown cause, silence is not evidence |
| `no_known_close` | a chain exists but no close was recorded (should not occur in practice) |

A reconnect inside the interval sets `feed_steady` to false, because ticks the service may
have missed during it are indistinguishable from an interval in which nothing traded.

## Reading the results

```bash
# How many intervals would have been filled, per ticker?
jq -r 'select(.would_fill) | .ticker' empty_interval_audit.jsonl | sort | uniq -c | sort -rn

# Per ticker and per day
jq -r 'select(.would_fill) | "\(.bucket_utc[0:10]) \(.ticker)"' \
  empty_interval_audit.jsonl | sort | uniq -c

# Why were intervals rejected?
jq -r 'select(.would_fill | not) | .engine_reason' \
  empty_interval_audit.jsonl | sort | uniq -c | sort -rn

# Which conditions did the worker refuse on, where the engine said yes?
jq -r 'select(.engine_reason == "would_fill" and (.would_fill | not))
       | "feed=\(.feed_steady) sub=\(.subscribed_throughout) session=\(.inside_session)"' \
  empty_interval_audit.jsonl | sort | uniq -c
```

The number that decides the question is the first one, read as a share of the session:
a full regular session is 390 one-minute intervals, an extended one 960.

## Costs and limits

- **Disk.** One line per empty interval per ticker. There is no rotation — delete the file
  when the measurement is finished, or point `EMPTY_INTERVAL_AUDIT_PATH` somewhere
  disposable.
- **The file is append-only across restarts.** After a restart the chain starts empty, so
  the first intervals of each ticker record `no_previous_candle`. That is not a defect; it
  is exactly the condition that would stop a real fill too.
- **It measures the current configuration.** `CANDLE_INTERVAL_MINUTES` changes the size of
  an interval and therefore every number here.
- **It is not a rehearsal of retention.** Filling for real would consume `MAX_CANDLES_STORED`
  slots; the audit writes nothing to the candles table and so shows nothing about that.
  A full regular session at one-minute intervals needs about 390 stored candles per ticker,
  an extended one about 960, against a default of 100.
