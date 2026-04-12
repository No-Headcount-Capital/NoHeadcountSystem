"""Runtime risk manager for live/sim OMS flows."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import DefaultDict

from src.infrastructure.models import Direction, Offset, OrderType, Signal, TickData, TradeData

from .policy import RiskPolicy, RiskRejectCode


class RuntimeRiskManager:
    """Risk manager that validates signals and tracks runtime risk state."""

    def __init__(self, policy: RiskPolicy | None = None, analytics_level2_scale: float = 0.5) -> None:
        self.policy = policy or RiskPolicy()

        self._symbol_net_positions: dict[str, float] = defaultdict(float)
        self._strategy_net_positions: dict[str, float] = defaultdict(float)
        self._symbol_mark_prices: dict[str, float] = {}

        self._strategy_order_timestamps: DefaultDict[str, deque[float]] = defaultdict(deque)
        self._strategy_reject_streak: dict[str, int] = defaultdict(int)
        self._strategy_cooldown_until: dict[str, float] = {}

        self._kill_switch_active = False
        self._kill_switch_reason = ""

        self._equity = self.policy.initial_equity
        self._peak_equity = self.policy.initial_equity
        self._analytics_level: int = 0
        self._analytics_reason: str = "NORMAL"
        self._analytics_level2_scale = min(max(analytics_level2_scale, 0.1), 1.0)
        self._active_limit_scale: float = 1.0

    def validate_signal(self, signal: Signal, now_ts: float | None = None) -> tuple[bool, str]:
        """Validate signal with stable reject reason codes."""
        ts = now_ts if now_ts is not None else time.time()

        if self._kill_switch_active:
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.KILL_SWITCH_ACTIVE

        if self._in_strategy_cooldown(signal.strategy_name, ts):
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.STRATEGY_IN_COOLDOWN

        if self.policy.enable_auto_kill_switch and self.policy.max_drawdown > 0:
            if self.get_drawdown() >= self.policy.max_drawdown:
                self.activate_kill_switch("AUTO_DRAWDOWN")
                self.on_signal_rejected(signal.strategy_name)
                return False, RiskRejectCode.MAX_DRAWDOWN

        if signal.volume <= 0:
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.INVALID_VOLUME

        if signal.order_type == OrderType.LIMIT and signal.price <= 0:
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.INVALID_LIMIT_PRICE

        if signal.volume > self._scaled_limit(self.policy.max_order_volume):
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.MAX_ORDER_VOLUME

        if signal.volume > self._scaled_limit(self.policy.max_strategy_order_volume):
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.MAX_STRATEGY_ORDER_VOLUME

        signed_volume = signal.volume if signal.direction == Direction.LONG else -signal.volume
        symbol_current = self._symbol_net_positions.get(signal.vt_symbol, 0.0)
        strategy_current = self._strategy_net_positions.get(signal.strategy_name, 0.0)

        if signal.offset == Offset.CLOSE:
            ok, reject_code = self._validate_close_semantics(signal, symbol_current)
            if not ok:
                self.on_signal_rejected(signal.strategy_name)
                return False, reject_code

        symbol_projected = symbol_current + signed_volume
        if abs(symbol_projected) > self._scaled_limit(self.policy.max_symbol_position):
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.MAX_SYMBOL_POSITION

        strategy_projected = strategy_current + signed_volume
        if abs(strategy_projected) > self._scaled_limit(self.policy.max_strategy_position):
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.MAX_STRATEGY_POSITION

        ref_price = self._resolve_reference_price(signal)
        if self._notional_limit_enabled() and ref_price is None:
            self.on_signal_rejected(signal.strategy_name)
            return False, RiskRejectCode.MISSING_REFERENCE_PRICE

        if ref_price is not None:
            order_notional = abs(ref_price * signal.volume)
            if self.policy.max_notional_per_order > 0 and order_notional > self.policy.max_notional_per_order:
                self.on_signal_rejected(signal.strategy_name)
                return False, RiskRejectCode.MAX_ORDER_NOTIONAL

            if self.policy.max_total_notional > 0:
                projected_total = self._projected_total_notional(signal.vt_symbol, symbol_projected, ref_price)
                if projected_total > self.policy.max_total_notional:
                    self.on_signal_rejected(signal.strategy_name)
                    return False, RiskRejectCode.MAX_TOTAL_NOTIONAL

        if self.policy.max_orders_per_minute > 0:
            self._trim_old_order_timestamps(signal.strategy_name, ts)
            if len(self._strategy_order_timestamps[signal.strategy_name]) >= self.policy.max_orders_per_minute:
                self.on_signal_rejected(signal.strategy_name)
                return False, RiskRejectCode.MAX_ORDERS_PER_MINUTE

        self._strategy_order_timestamps[signal.strategy_name].append(ts)
        self._strategy_reject_streak[signal.strategy_name] = 0
        return True, ""

    def on_signal_rejected(self, strategy_name: str, now_ts: float | None = None) -> None:
        """Track reject streak and trigger strategy cooldown when configured."""
        if self.policy.max_consecutive_rejections <= 0:
            return
        ts = now_ts if now_ts is not None else time.time()
        streak = self._strategy_reject_streak.get(strategy_name, 0) + 1
        self._strategy_reject_streak[strategy_name] = streak
        if streak >= self.policy.max_consecutive_rejections:
            self._strategy_cooldown_until[strategy_name] = ts + self.policy.cooldown_seconds
            self._strategy_reject_streak[strategy_name] = 0

    def on_trade(self, trade: TradeData) -> None:
        """Update risk state with a filled trade."""
        signed_volume = trade.volume if trade.direction == Direction.LONG else -trade.volume
        self._symbol_net_positions[trade.vt_symbol] = self._symbol_net_positions.get(trade.vt_symbol, 0.0) + signed_volume
        self._strategy_net_positions[trade.strategy_name] = (
            self._strategy_net_positions.get(trade.strategy_name, 0.0) + signed_volume
        )
        self._symbol_mark_prices[trade.vt_symbol] = trade.price

    def on_order_filled(self, vt_symbol: str, direction: Direction, volume: float, strategy_name: str = "") -> None:
        """Compatibility helper for existing OMS call pattern."""
        signed_volume = volume if direction == Direction.LONG else -volume
        self._symbol_net_positions[vt_symbol] = self._symbol_net_positions.get(vt_symbol, 0.0) + signed_volume
        if strategy_name:
            self._strategy_net_positions[strategy_name] = (
                self._strategy_net_positions.get(strategy_name, 0.0) + signed_volume
            )

    def on_tick(self, tick: TickData) -> None:
        """Refresh mark price for notional checks."""
        self._symbol_mark_prices[tick.vt_symbol] = tick.last_price

    def mark_equity(self, equity: float) -> None:
        """Mark account equity for drawdown-based kill switch checks."""
        self._equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def get_drawdown(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, self._peak_equity - self._equity)

    def activate_kill_switch(self, reason: str = "MANUAL") -> None:
        if not self.policy.enable_kill_switch:
            return
        self._kill_switch_active = True
        self._kill_switch_reason = reason

    def deactivate_kill_switch(self) -> None:
        self._kill_switch_active = False
        self._kill_switch_reason = ""

    def apply_analytics_metrics(self, metrics) -> None:
        """Apply analytics risk level to runtime controls."""
        level = int(getattr(metrics, "level", 0))
        reason = str(getattr(metrics, "level_reason", "UNKNOWN"))
        equity = float(getattr(metrics, "equity", 0.0) or 0.0)
        if equity > 0:
            self.mark_equity(equity)

        self._analytics_level = level
        self._analytics_reason = reason

        if level >= 3:
            self._active_limit_scale = self._analytics_level2_scale
            self.activate_kill_switch("ANALYTICS_LEVEL3")
            return

        if self._kill_switch_reason == "ANALYTICS_LEVEL3":
            self.deactivate_kill_switch()

        if level >= 2:
            self._active_limit_scale = self._analytics_level2_scale
        else:
            self._active_limit_scale = 1.0

    def get_symbol_positions(self) -> dict[str, float]:
        return dict(self._symbol_net_positions)

    def get_mark_prices(self) -> dict[str, float]:
        return dict(self._symbol_mark_prices)

    def get_equity(self) -> float:
        return self._equity

    def snapshot(self) -> dict[str, object]:
        return {
            "kill_switch_active": self._kill_switch_active,
            "kill_switch_reason": self._kill_switch_reason,
            "drawdown": self.get_drawdown(),
            "equity": self._equity,
            "peak_equity": self._peak_equity,
            "analytics_level": self._analytics_level,
            "analytics_reason": self._analytics_reason,
            "active_limit_scale": self._active_limit_scale,
            "symbol_net_positions": dict(self._symbol_net_positions),
            "strategy_net_positions": dict(self._strategy_net_positions),
            "strategy_cooldown_until": dict(self._strategy_cooldown_until),
        }

    def _validate_close_semantics(self, signal: Signal, symbol_position: float) -> tuple[bool, str]:
        if signal.direction == Direction.SHORT:
            if symbol_position <= 0:
                return False, RiskRejectCode.CLOSE_WITHOUT_POSITION
            if signal.volume > symbol_position:
                return False, RiskRejectCode.CLOSE_VOLUME_EXCEEDS_POSITION
        else:
            if symbol_position >= 0:
                return False, RiskRejectCode.CLOSE_WITHOUT_POSITION
            if signal.volume > abs(symbol_position):
                return False, RiskRejectCode.CLOSE_VOLUME_EXCEEDS_POSITION
        return True, ""

    def _resolve_reference_price(self, signal: Signal) -> float | None:
        if signal.order_type == OrderType.LIMIT:
            return signal.price if signal.price > 0 else None
        mark = self._symbol_mark_prices.get(signal.vt_symbol)
        if mark is not None and mark > 0:
            return mark
        if signal.price > 0:
            return signal.price
        return None

    def _notional_limit_enabled(self) -> bool:
        return self.policy.max_notional_per_order > 0 or self.policy.max_total_notional > 0

    def _projected_total_notional(self, vt_symbol: str, projected_position: float, fallback_price: float) -> float:
        total = 0.0
        for symbol, position in self._symbol_net_positions.items():
            price = self._symbol_mark_prices.get(symbol, 0.0)
            if price > 0:
                total += abs(position * price)
        symbol_price = self._symbol_mark_prices.get(vt_symbol, fallback_price)
        old_position = self._symbol_net_positions.get(vt_symbol, 0.0)
        total -= abs(old_position * symbol_price)
        total += abs(projected_position * symbol_price)
        return total

    def _trim_old_order_timestamps(self, strategy_name: str, now_ts: float) -> None:
        dq = self._strategy_order_timestamps[strategy_name]
        cutoff = now_ts - 60.0
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _in_strategy_cooldown(self, strategy_name: str, now_ts: float) -> bool:
        cooldown_until = self._strategy_cooldown_until.get(strategy_name, 0.0)
        if cooldown_until <= now_ts:
            if strategy_name in self._strategy_cooldown_until:
                self._strategy_cooldown_until.pop(strategy_name, None)
            return False
        return True

    def _scaled_limit(self, limit_value: float) -> float:
        if limit_value <= 0:
            return limit_value
        return limit_value * self._active_limit_scale
