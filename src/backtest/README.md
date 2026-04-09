# Kline Backtest Framework: Matching Logic

This folder contains a Numba-accelerated kline backtest core.
The matching engine lives mainly in `exchange.py`, and is driven by the event loop in `loop.py`.

## Core Components

- `KlineExchange` (`exchange.py`): order storage, matching, position/PnL accounting.
- `Order`/`Kline`/`OpenSignal` (`events.py`): typed event payloads for strategy callbacks.
- `kline_backtest` loop (`loop.py`): drives per-bar event order and calls the exchange.

## Supported Order Types

- Market order:
  - Executed immediately at the current `reference_price`.
  - Charged `taker_commission`.
- Limit order:
  - Stored as `[price, quantity, order_id]` in a fixed-size array.
  - Positive `quantity` = buy; negative `quantity` = sell.
  - Charged `maker_commission` when filled.

## Aggressive Limit Conversion

When placing a limit order, the engine first checks whether the order is aggressively priced against current `reference_price`:

- Buy limit with `price > reference_price` -> converted to market buy immediately.
- Sell limit with `price < reference_price` -> converted to market sell immediately.

Equality does not auto-convert (`price == reference_price` stays as a normal limit order).

If not marketable, the order stays in the pending order array.

## Bar-Level Matching Sequence

For each bar `i` in `loop.py`:

1. Signal phase (only when `signal_flags[i] == 1` and `i > 0`):
   - Set `reference_price = opens[i]`.
   - Call `strategy.on_kline(OpenSignal(...), exchange)`.
   - Strategy can place/cancel orders here.

2. High-price sweep:
   - Call `exchange.match_limit_order(highs[i])`.
   - Any order satisfying trigger condition is filled.
   - If at least one fill happens in this sweep, `strategy.on_order(filled_order, exchange)` is called once with the last filled order.

3. Low-price sweep:
   - Call `exchange.match_limit_order(lows[i])`.
   - Same per-sweep callback behavior as above.

4. Tick phase:
   - Set `reference_price = closes[i]`.
   - Call `strategy.on_tick(Kline(...), exchange)`.

Important: intra-bar path is approximated as two trigger checks (`high` then `low`), not tick-by-tick replay.

## Limit Fill Condition and Fill Price

For each live limit order:

- Buy limit (`quantity > 0`) fills when `bar_price <= order_price`.
- Sell limit (`quantity < 0`) fills when `bar_price >= order_price`.

If triggered, it is executed at **order price** (not at `high`/`low`), then removed from active orders.

## PnL and Position Accounting

`_update_trade(price, trade_size)` in `KlineExchange` updates:

- `position`
- `cost_price` (average cost for same-direction adds)
- `realized_total_pnl` and `realized_trade_pnls` (when reducing/reversing)
- `turnover`

Unrealized PnL at mark price `p` is:

`(p - cost_price) * position`

Total marked PnL used in the loop snapshot is:

`realized_total_pnl + unrealized_pnl`

## Commission Model

- Market fills: subtract `abs(quantity * price) * taker_commission` from realized PnL.
- Limit fills: subtract `abs(quantity * price) * maker_commission` from realized PnL.

## Order Management Details

- `cancel_order(order_id)`: remove one active order.
- `cancel_all_order()`: clear all active orders.
- `query_order(order_id)`: returns internal index if active, `-1` if not found.
- Capacity is fixed by `max_orders`; new limit orders fail if full.

## Modeling Assumptions and Caveats

- No partial fills: each triggered order is fully filled in one step.
- No slippage or queue priority simulation.
- No explicit bid/ask spread; matching uses kline OHLC values.
- Bar path ambiguity is simplified to `high` pass then `low` pass.
- `match_limit_order` may fill multiple orders in one pass, but only the last filled `Order` object is returned to `on_order`.

These assumptions make the engine fast and deterministic, but may differ from live microstructure.

## Minimal Strategy Callback Contract

The strategy object should provide methods compatible with the loop:

- `on_kline(open_signal, exchange)`
- `on_order(filled_order, exchange)`
- `on_tick(kline, exchange)`

Inside callbacks, strategies can call exchange APIs such as:

- `place_market_order(quantity)`
- `place_limit_order(price, quantity)`
- `cancel_order(order_id)`
- `cancel_all_order()`

## Quick Run Entry

Use `run_kline_backtest(...)` from `runner.py` as the convenience entry point.
It compiles/binds the strategy type and executes the backtest loop.
