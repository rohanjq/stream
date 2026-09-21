# Production AI host architecture

## Purpose

The host is a durable event-driven worker, not one unbounded chat prompt. It
can run scheduled market narration, answer selected viewers, welcome
subscribers, process verified market events, and accept future job types while
keeping each job's context deliberately scoped.

OHLC remains authoritative for candles and prices. Signals remains
authoritative for indicators. The host does not calculate or repair either.

## Runtime and durability

The initial single-host deployment runs the worker inside the scene service so
speech handoff uses the existing bounded priority queue without another network
hop. A WAL-mode SQLite database on the persistent `stream-state` volume stores:

- deduplicated input events;
- durable jobs, attempts, leases, terminal results and errors;
- daily broadcast and per-viewer turns;
- versioned compact summaries and opt-in person memories;
- workflow traces.

LangGraph provides workflow transitions. Scheduling, context selection,
validation and side effects remain deterministic application code; model
output cannot create jobs or call arbitrary tools. The store API is kept narrow
so a horizontally scaled deployment can move the worker to a separate service
and PostgreSQL without changing handlers.

On restart, a job whose lease expired is reclaimed. Stable event and job IDs
prevent duplicate comments, welcomes and scheduled slots.

## Context scopes

Every registered job declares one scope:

| Scope | Inputs | Examples |
| --- | --- | --- |
| `none` | Event payload only | Subscriber welcome, fixed announcement |
| `broadcast` | Daily summary and recent public turns | Show recap |
| `topic` | Broadcast context plus fresh authoritative tools | Market narration or event |
| `person` | Broadcast context plus activated viewer memory | Personal reply/compaction |

Thread IDs are stable: `broadcast:<UTC-date>` and `viewer:<channel-id>`. Raw
turns are retained as the audit source; summaries never replace price, signal,
job or tool records.

The daily broadcast is compacted hourly. Recent turns remain verbatim while an
older range is summarized with its ending turn ID. A failed compaction leaves
the previous version active. Day rollover naturally starts a new broadcast
thread.

## Person memory

Messages are deduplicated by YouTube message ID. Event insertion, viewer count
and both transcript writes happen in one transaction, avoiding half-written
memory after a crash.

Long-term viewer memory is unavailable until ten accepted comments from the
stable channel ID. At ten comments, and every five thereafter, a background
job compacts explicitly stated interests and preferences. It must not infer or
store demographics, finances, location, identity or other sensitive traits.
Display names are presentation data, not identity keys.

## Initial two-minute market job

The first enabled job uses AllTick's verified `OHLC_SYMBOL=BTCUSDT`, is labelled
Bitcoin, and runs every 120 seconds.

Each slot has one durable ID:

```text
market:<symbol>:<slot-epoch>
```

The worker speaks only the current slot. Older missed slots are recorded as
expired, so recovery never produces a burst of stale narration. Scheduled
speech is low priority.

Before generation it requires:

- an active OHLC connection;
- a current 1-minute source timestamp, not merely a recently received seed;
- a price no more than 120 seconds old;
- a closed 5-minute candle;
- optional supplied EMA 50 and EMA 200 values from Signals.

The formatted price is injected outside the model. Generated output must
contain that exact string and may not add another number, prediction, trade
call or unsupported indicator. Failure uses a deterministic verified template.
The initial requested policy speaks once per fresh two-minute slot; novelty
suppression can be added later without changing scheduling.

## External events and future jobs

Authenticated callers submit stable IDs to `/api/agent/events`:

- `subscription` uses `none` scope and gives a context-free welcome;
- `market_event` uses `topic` scope and receives bounded public context.

New jobs are added through the handler registry with an explicit context scope,
input schema, priority, idempotency rule, freshness/expiry policy and allowed
side effects. Future calendar events, news alerts, educational indicator
segments, recaps and personas therefore do not require one giant prompt.

## Failure and safety policy

- Provider timeout or rate limit: use bounded retries at the job layer and a
  verified deterministic template where safe.
- Stale or mismatched market data: skip speech and record the reason.
- Queue overload: preserve higher-priority viewer/operator work and expire old
  scheduled narration.
- Duplicate or out-of-order delivery: dedupe by source ID and retain source
  timestamps; never replay completed side effects.
- Prompt injection: external text is untrusted JSON context, cannot replace
  system policy, and cannot register or execute tools.
- Secrets never enter prompts, traces or status responses.

## Operations

`GET /api/agent/status` reports enabled state, workflow backend, configured
symbol, feed freshness, schedule interval, memory threshold and job counts.
Runs link the source job to a workflow trace and the queued speech metadata.
Production monitoring should alert on stale feed age, failed/retried jobs,
queue drops, model fallbacks, compaction failures and repeated lease recovery.

Roll out future job types in shadow/text-only mode first, compare every factual
claim with its source, then enable low-priority speech. Privileged actions must
remain allowlisted and require explicit operator policy or approval.
