# Market Making Simulator (CLOB + Microstructure Game)

A high-fidelity **market-making game / simulator** implementing a Central Limit Order Book (CLOB), deterministic price-time matching, latency/toxicity-aware bots, real-time risk controls, and replayable sessions for analysis.

This project is built to feel like an electronic market: queue priority, spread dynamics, adverse selection, maker/taker economics, and rounds that progressively reveal information and force repricing.

---

## Table of contents

- [What this is](#what-this-is)
- [Architecture](#architecture)
- [Core market model](#core-market-model)
- [Microstructure & signals](#microstructure--signals)
- [Bots](#bots)
- [Risk management](#risk-management)
- [Analytics](#analytics)
- [Sessions, checkpoints, replay](#sessions-checkpoints-replay)
- [GUI control plane](#gui-control-plane)
- [Running](#running)
- [Testing](#testing)
- [Performance & determinism notes](#performance--determinism-notes)
- [Extending the simulator](#extending-the-simulator)

---

## What this is

Most toy sims skip the hard parts: matching correctness, deterministic behavior, and microstructure feedback loops.

This simulator focuses on:
- **Exchange mechanics**: book storage, matching priority, execution pricing, IOC semantics.
- **Behavior under uncertainty**: a “fair value” that evolves as information is revealed over rounds.
- **Market ecology**: multiple agent types interacting on a shared tape with latency/risk constraints.

---

## Architecture

The codebase is intentionally layered so the exchange can run headless and the UI is replaceable.

### Layer map

- **engine/** (Domain layer)
  - `order_book.py`: thread-safe CLOB storage + order indices
  - `matching_engine.py`: deterministic FIFO matching + maker/taker + self-trade prevention
  - `trader.py`: trader state, fill history, P&L, toxicity score
  - `risk_manager.py`: pre-trade risk checks + runtime liquidation rules
  - `bot_strategies.py`: bot decision logic (latency, stickiness, toxicity response, tape usage)

- **application/** (Orchestration layer)
  - `market_simulator.py`: game lifecycle + tick loop + events + snapshot boundary
  - `session_manager.py`: session registry + checkpoint save/load
  - `replay_manager.py`: JSONL event/command recording + playback
  - `analytics_engine.py`: stateless performance attribution/reporting

- **infrastructure/** (I/O + configuration)
  - `config.py`: difficulty presets (fees, quote lifetime, bot latency multiplier, toxicity threshold, volatility cap)
  - `persistence.py`: atomic JSON/JSONL writes + safe serialization
  - `logger.py`: structured logging config (console + optional rotating file)

- **ui/** (Presentation layer)
  - Desktop GUI with a controller that drives `tick()` and renders `MarketSnapshot`

### Data flow (high level)

```mermaid
flowchart LR
  UI[GUI Widgets] -->|Commands| Controller[MarketController]
  Controller -->|tick() via timer| Sim[MarketSimulator]

  Sim -->|Add/Cancel| Book[OrderBook]
  Sim -->|Match once per tick| Match[MatchingEngine]
  Match -->|MatchEvent stream| Sim

  Sim -->|apply_fill()| Trader[Trader]
  Sim -->|risk checks| Risk[RiskManager]
  Sim -->|MarketSnapshot| Controller
  Controller -->|snapshot_updated| UI

  Sim -->|MarketEvent stream| Replay[ReplayManager]
  Sim -->|reports| Analytics[AnalyticsEngine]
---

## Core market model

### 1) Order book (CLOB)

The order book is a price-level map where each price level is a FIFO queue:
    price -> deque[Order] for bids and asks

Each order has a monotonically increasing `order_id` (for deterministic cancels and reproducibility).

Key design points:

- Tick snapping: prices are snapped to `min_tick_size` for stable dict keys.
- Fast cancels:
  - `order_id -> (side, price, trader_id)` index
  - `trader_id -> set(order_id)` index
- Expiry: `expire_orders(now)` removes stale quotes based on `quote_lifetime`.

### 2) Matching engine (price-time priority)

The matching engine uses standard FIFO semantics:

- Price priority: best price matches first
- Time priority: earlier `(timestamp, order_id)` wins within a level
- No pro-rata: this is FIFO within price level (important for “queue position” gameplay)

Maker/taker attribution:

- The older order by `(timestamp, order_id)` is the maker
- The newer order is the taker
- Execution price is the maker’s resting price (maker-price execution)

Self-trade prevention:

- If top-of-book bid and ask belong to the same trader, the engine removes the taker-side order deterministically and continues.

### 3) Tick loop (single source of truth)

`MarketSimulator.tick()` is the orchestration heartbeat. It is explicitly structured so that matching occurs exactly once per tick.

Pseudo sequence:

- Expire stale quotes
- Compute current fair value and uncertainty
- Bots:
  - update quotes (cancel+replace style)
  - optionally submit IOC orders
- Run matching once (`match_orders`)
- Apply fills to both counterparties and update toxicity scores
- Cancel leftover IOC orders by `order_id` (true IOC semantics)
- Volatility feedback + risk checks
- Emit snapshot to UI subscribers

```text
sequenceDiagram
  participant UI as UI / Controller
  participant SIM as MarketSimulator
  participant BOOK as OrderBook
  participant BOT as BotManager
  participant ME as MatchingEngine
  participant RISK as RiskManager

  UI->>SIM: tick()
  SIM->>BOOK: expire_orders(now)
  SIM->>SIM: compute fair_value / uncertainty
  SIM->>BOT: update_quotes(..., tape, user_toxicity, risk_manager)
  BOT-->>SIM: ioc_order_ids[]
  SIM->>ME: match_orders(now)
  ME-->>SIM: MatchEvent[]
  SIM->>SIM: apply fills + fees + toxicity update
  SIM->>BOOK: cancel_order_by_id(ioc_ids...)
  SIM->>RISK: check_margin_call(...) for traders
  SIM-->>UI: MarketSnapshot

  ---

## Microstructure & signals

This simulator exposes and uses several microstructure primitives:

- **L1 / L2 state**
  - best bid/ask, spread, mid
  - limited depth snapshots (top N levels)

- **Queue priority**
  - FIFO at each price level is preserved; order placement timing matters

- **Order flow tape**
  - bots receive recent `TradePrint` objects (price, qty, aggressor side) to compute flow imbalance

- **Adverse selection / toxicity**
  - traders maintain an adverse selection score intended to reflect “fill quality”
  - bots can widen or pull quotes as toxicity rises

- **Volatility feedback**
  - volatility evolves through round events (information reveals) and trading activity

---

## Bots

Bots are implemented as strategy objects producing:

- passive quotes: `(bid_px, ask_px)`
- optional aggressive IOC orders: `[(side, qty), ...]`

Representative agent types:

- **Market maker**
  - two-sided quoting
  - spread widening/skew based on inventory + volatility
  - reacts to toxicity (defensive widening / reduced quoting)

- **Momentum trader**
  - EMA-based trend filter on mid
  - tape-based flow imbalance signal
  - fires IOC in direction of signal with configurable aggression

- **Arbitrageur**
  - compares theoretical fair value vs observed market prices
  - trades mispricings and mean reversion opportunities

Latency model:

- bots gate actions using a latency schedule with jitter
- difficulty presets scale latency via a multiplier

---

## Risk management

Risk is deliberately separated from strategy logic and handled centrally.

Implemented checks:

- Position limit (absolute position cap)
- Max order size (“fat finger” protection)
- Margin call: if MTM P&L < threshold, force liquidation and flatten position at a penalty price
- Loss limit: circuit breaker signal for “stop trading”
- VAR estimate: simplified volatility-based calculation from recent fills
- Concentration check: compares order size vs total book depth

Risk metrics are exposed to the UI via snapshots (position utilization, margin cushion, VAR, at-risk flag).

---

## Analytics

Analytics are designed as pure, stateless computations:

- **P&L attribution**
  - gross vs net
  - realized vs unrealized (simplified model)
  - fee impact

- **Execution quality**
  - VWAP
  - average edge
  - adverse fill rate
  - VWAP vs settlement / fair value

- **Risk-adjusted returns**
  - Sharpe, Sortino
  - max drawdown
  - volatility estimate

This is intentionally structured so analytics can be computed:

- on-demand for UI
- offline from replay artifacts
- in batch for benchmarking and regression tests

---

## Sessions, checkpoints, replay

Reproducibility is treated as a first-class feature.

### Sessions (seeded)

Sessions are created with a seed:

- If no seed is provided, one is derived from time.
- Seeds are passed into the simulator so digit generation is deterministic per session.

### Checkpoints (resume UX)

Checkpoint saving stores:

- session metadata (including seed)
- last snapshot (to resume the UI quickly)

Checkpoint loading:

- reconstructs a simulator with the same seed so settlement digits remain consistent
- does not fully restore internal order book state (that’s the job of replay, if desired)

### Replay (JSONL event stream)

Replay manager records to JSONL:

- header record
- event records (`MarketEvents`)
- command records (user actions)
- optional snapshot records

There are two replay modes:

- **Visual replay**
  - record snapshots periodically and play them back
  - no deterministic re-simulation required

- **Deterministic re-sim (future hardening)**
  - requires injecting a time source into the simulator
  - requires elimination of hidden nondeterminism

---

## GUI control plane

The GUI uses a single controller object as the “owner” of the simulator instance.

Key ideas:

- The controller drives `tick()` on a timer (5Hz heartbeat).
- The UI is updated through an immutable snapshot boundary object (`MarketSnapshot`).
- UI commands are write-only (make market, lift, hit, cancel) and then the engine ticks.

The GUI renders:

- order book depth table
- position / P&L panel
- digit reveal panel
- event log and trade log
- leaderboard and game-over dialogs during intermission or completion

---

## Running

### Headless run (saves checkpoint + replay artifacts)

```bash
python main.py

What it does:

- configures logging
- starts a session
- attaches replay recording
- runs a short tick loop
- saves:
  - `runs/last_checkpoint.json`
  - `runs/last_replay.jsonl`

### GUI

```bash
python gui_play.py

## Testing

Tests are treated as **exchange invariants** and should remain strict.

Coverage includes:

- **matching correctness**
  - price priority
  - time priority
  - partial fills across levels
  - self-trade prevention
  - maker / taker identification
  - determinism (same inputs → same outputs)

- **order book behavior**
  - add / cancel / expire
  - index correctness

- **trader accounting**
  - fills
  - P&L
  - VWAP

- **risk checks**
  - limits
  - margin call behavior
  - metric correctness

Run all tests:

```bash
pytest -q

## Performance & determinism notes

This simulator favors determinism and correctness over raw throughput.

Notes:

- Best bid / ask currently uses `max()` / `min()` on dict keys, which is `O(levels)` per retrieval.
  - For large books, upgrade to a sorted structure (heap or tree) while keeping FIFO queues per level.

- IOC semantics are enforced by:
  - collecting IOC order IDs created during bot actions
  - cancelling leftovers by `order_id` after matching

- True deterministic replay is easiest if the simulator’s clock is injectable (so `now` is controlled).

---

## Extending the simulator

### Add a new bot strategy

- Create a `BaseBot` subclass and implement `decide(...)`.
- Register it in the bot roster in `BotManager`.
- Ensure:
  - position limits are respected
  - pre-trade risk checks run before aggressive orders
  - IOC order IDs are returned when IOC behavior is intended

### Add a new metric / report

- Add a pure function to `AnalyticsEngine`.
- Wire it into the report generator.

### Add a new UI (web / TUI)

- Consume `MarketSnapshot`.
- Issue commands to the simulator through a thin controller layer.
- Keep the engine headless and UI-agnostic.

