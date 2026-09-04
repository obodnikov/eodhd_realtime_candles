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
  "reason": "would_fill",
  "chain_intact": true,
  "feed_steady": true,
  "subscribed_throughout": true,
  "inside_session": true,
  "price": 153.5,
  "observed_at": "2026-09-02T09:17:03.412870+00:00"
}
```

`would_fill` is the answer: true only when every condition below held.

| Field | Meaning |
| --- | --- |
| `reason` | `would_fill`, or the first condition that failed — see below |
| `chain_intact` | the preceding interval was covered, by a real candle or by one this audit judged fillable |
| `feed_steady` | the feed was connected at both ends of the interval with an unchanged connection count |
| `subscribed_throughout` | the ticker was subscribed at both ends |
| `inside_session` | the interval starts inside the configured window |
| `price` | the last traded close — what a synthetic candle would carry in all four price fields |

`reason` values, in the order they are tested:

| Value | Meaning |
| --- | --- |
| `no_previous_candle` | this ticker has produced no candle yet, so there is no chain and no known price |
| `chain_broken` | the preceding interval was not covered — after a hole of unknown cause, silence is not evidence |
| `no_known_close` | a chain exists but no close was recorded (should not occur in practice) |
| `outside_session` | the interval falls outside the configured window |
| `feed_unsteady` | the connection dropped or was remade during the interval |
| `subscription_changed` | the ticker was not subscribed at both ends |
| `would_fill` | every condition held |

A reconnect inside the interval sets `feed_steady` to false, because ticks the service may
have missed during it are indistinguishable from an interval in which nothing traded.

## How the chain is tracked, and why it is not the engine's job

A real fill writes a candle for an empty interval, so the chain carries forward and a run
of silent intervals is filled end to end. The audit therefore keeps its own record of the
latest interval covered per ticker, advancing it both for real candles and for intervals it
judges fillable — exactly as a real fill would.

This matters for the count, not just for tidiness. Asking the engine about the chain
instead would break it after the first silent interval, because nothing was actually
written: a run of five silent minutes would be counted as one. In the first measurement
run that understated the extended session roughly fivefold. Fixed in v0.9.12; numbers
gathered before that release are floors, not totals.

## Reading the results

```bash
# How many intervals would have been filled, per ticker?
jq -r 'select(.would_fill) | .ticker' empty_interval_audit.jsonl | sort | uniq -c | sort -rn

# Per ticker and per day
jq -r 'select(.would_fill) | "\(.bucket_utc[0:10]) \(.ticker)"' \
  empty_interval_audit.jsonl | sort | uniq -c

# Why were intervals rejected?
jq -r 'select(.would_fill | not) | .reason' \
  empty_interval_audit.jsonl | sort | uniq -c | sort -rn

# How long are the runs of silence, per ticker?
jq -r 'select(.would_fill) | .ticker' empty_interval_audit.jsonl \
  | sort | uniq -c | sort -rn | head -20
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
