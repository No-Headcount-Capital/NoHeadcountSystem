"""Numba-compatible risk guard for backtest loops."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from numba import njit
except Exception:  # pragma: no cover - fallback when numba unavailable
    def njit(*args, **kwargs):  # type: ignore
        def _decorator(func):
            return func

        return _decorator


RISK_OK = 0
RISK_INVALID_VOLUME = 1
RISK_INVALID_LIMIT_PRICE = 2
RISK_CLOSE_WITHOUT_POSITION = 3
RISK_CLOSE_VOLUME_EXCEEDS_POSITION = 4
RISK_MAX_ORDER_VOLUME = 5
RISK_MAX_STRATEGY_ORDER_VOLUME = 6
RISK_MAX_SYMBOL_POSITION = 7
RISK_MAX_STRATEGY_POSITION = 8
RISK_MISSING_REFERENCE_PRICE = 9
RISK_MAX_ORDER_NOTIONAL = 10
RISK_MAX_TOTAL_NOTIONAL = 11
RISK_MAX_ORDERS_PER_MINUTE = 12


@njit(cache=True)
def validate_order_numba(
    quantity: float,
    is_limit_order: bool,
    price: float,
    direction_sign: float,
    is_close: bool,
    symbol_position: float,
    strategy_position: float,
    max_order_volume: float,
    max_strategy_order_volume: float,
    max_symbol_position: float,
    max_strategy_position: float,
    max_notional_per_order: float,
    max_total_notional: float,
    current_total_notional: float,
    reference_price: float,
    orders_in_last_minute: int,
    max_orders_per_minute: int,
) -> int:
    if quantity <= 0:
        return RISK_INVALID_VOLUME

    if is_limit_order and price <= 0:
        return RISK_INVALID_LIMIT_PRICE

    if quantity > max_order_volume:
        return RISK_MAX_ORDER_VOLUME

    if quantity > max_strategy_order_volume:
        return RISK_MAX_STRATEGY_ORDER_VOLUME

    if is_close:
        if direction_sign < 0:
            if symbol_position <= 0:
                return RISK_CLOSE_WITHOUT_POSITION
            if quantity > symbol_position:
                return RISK_CLOSE_VOLUME_EXCEEDS_POSITION
        else:
            if symbol_position >= 0:
                return RISK_CLOSE_WITHOUT_POSITION
            if quantity > abs(symbol_position):
                return RISK_CLOSE_VOLUME_EXCEEDS_POSITION

    signed_qty = quantity if direction_sign > 0 else -quantity
    projected_symbol = symbol_position + signed_qty
    projected_strategy = strategy_position + signed_qty

    if abs(projected_symbol) > max_symbol_position:
        return RISK_MAX_SYMBOL_POSITION

    if abs(projected_strategy) > max_strategy_position:
        return RISK_MAX_STRATEGY_POSITION

    est_price = price if is_limit_order else reference_price
    if (max_notional_per_order > 0 or max_total_notional > 0) and est_price <= 0:
        return RISK_MISSING_REFERENCE_PRICE

    if max_notional_per_order > 0:
        if abs(est_price * quantity) > max_notional_per_order:
            return RISK_MAX_ORDER_NOTIONAL

    if max_total_notional > 0:
        current_symbol_notional = abs(symbol_position * est_price)
        projected_symbol_notional = abs(projected_symbol * est_price)
        projected_total_notional = current_total_notional - current_symbol_notional + projected_symbol_notional
        if projected_total_notional > max_total_notional:
            return RISK_MAX_TOTAL_NOTIONAL

    if max_orders_per_minute > 0 and orders_in_last_minute >= max_orders_per_minute:
        return RISK_MAX_ORDERS_PER_MINUTE

    return RISK_OK


@dataclass(slots=True)
class BacktestRiskConfig:
    max_order_volume: float = 1.0
    max_symbol_position: float = 2.0
    max_strategy_order_volume: float = 1.0
    max_strategy_position: float = 2.0
    max_notional_per_order: float = 0.0
    max_total_notional: float = 0.0
    max_orders_per_minute: int = 0


class BacktestRiskGuard:
    """Thin python wrapper around the njit validator."""

    def __init__(self, config: BacktestRiskConfig | None = None) -> None:
        self.config = config or BacktestRiskConfig()
        self.symbol_positions: dict[str, float] = {}
        self.strategy_positions: dict[str, float] = {}
        self.symbol_reference_price: dict[str, float] = {}
        self.strategy_order_counts_last_minute: dict[str, int] = {}
        self.total_notional: float = 0.0

    def validate(
        self,
        *,
        strategy_name: str,
        vt_symbol: str,
        quantity: float,
        is_limit_order: bool,
        price: float,
        direction_sign: float,
        is_close: bool,
    ) -> int:
        symbol_position = self.symbol_positions.get(vt_symbol, 0.0)
        strategy_position = self.strategy_positions.get(strategy_name, 0.0)
        reference_price = self.symbol_reference_price.get(vt_symbol, 0.0)
        orders_last_minute = self.strategy_order_counts_last_minute.get(strategy_name, 0)

        return int(
            validate_order_numba(
                quantity=quantity,
                is_limit_order=is_limit_order,
                price=price,
                direction_sign=direction_sign,
                is_close=is_close,
                symbol_position=symbol_position,
                strategy_position=strategy_position,
                max_order_volume=self.config.max_order_volume,
                max_strategy_order_volume=self.config.max_strategy_order_volume,
                max_symbol_position=self.config.max_symbol_position,
                max_strategy_position=self.config.max_strategy_position,
                max_notional_per_order=self.config.max_notional_per_order,
                max_total_notional=self.config.max_total_notional,
                current_total_notional=self.total_notional,
                reference_price=reference_price,
                orders_in_last_minute=orders_last_minute,
                max_orders_per_minute=self.config.max_orders_per_minute,
            )
        )

    def apply_fill(self, *, strategy_name: str, vt_symbol: str, fill_price: float, signed_quantity: float) -> None:
        old_pos = self.symbol_positions.get(vt_symbol, 0.0)
        old_price = self.symbol_reference_price.get(vt_symbol, fill_price)
        self.total_notional -= abs(old_pos * old_price)

        new_pos = old_pos + signed_quantity
        self.symbol_positions[vt_symbol] = new_pos
        self.symbol_reference_price[vt_symbol] = fill_price
        self.total_notional += abs(new_pos * fill_price)

        self.strategy_positions[strategy_name] = self.strategy_positions.get(strategy_name, 0.0) + signed_quantity

    def update_reference_price(self, vt_symbol: str, price: float) -> None:
        position = self.symbol_positions.get(vt_symbol, 0.0)
        old_price = self.symbol_reference_price.get(vt_symbol, price)
        # Rebuild notional approximately for this symbol at the new mark price.
        self.total_notional -= abs(position * old_price)
        self.symbol_reference_price[vt_symbol] = price
        self.total_notional += abs(position * price)

    def mark_new_order(self, strategy_name: str, count_last_minute: int) -> None:
        self.strategy_order_counts_last_minute[strategy_name] = count_last_minute
