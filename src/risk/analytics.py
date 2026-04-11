"""Risk analytics service for intraday VaR/CVaR, stress tests, and volatility.

This module is intentionally standalone so it can be plugged into the existing
event flow without requiring immediate changes in other modules.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt
from statistics import mean, pstdev

from src.infrastructure.models import TickData


def _floor_to_interval(dt: datetime, minutes: int) -> datetime:
    base = dt.replace(second=0, microsecond=0)
    floored_minute = base.minute - (base.minute % minutes)
    return base.replace(minute=floored_minute)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    w = idx - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


@dataclass(slots=True)
class StressScenario:
    name: str
    default_shock: float
    per_symbol_shock: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class RiskAnalyticsConfig:
    bar_minutes: int = 10
    recompute_minutes: int = 10
    history_points: int = 2880  # ~20 days with 10-min bars
    confidence_levels: tuple[float, ...] = (0.95, 0.99)
    volatility_windows_bars: tuple[int, ...] = (144, 432)  # 24h, 3d on 10-min bars

    # Control thresholds based on ratios to equity (e.g. 0.03 = 3%)
    level1_var_ratio: float = 0.03
    level2_cvar_ratio: float = 0.05
    level3_stress_ratio: float = 0.10

    # Debounce settings
    upgrade_confirmations: int = 3
    downgrade_confirmations: int = 3
    hysteresis_ratio: float = 0.8

    # Default stress scenarios
    stress_scenarios: tuple[StressScenario, ...] = (
        StressScenario(name="ALL_SYMBOLS_-5", default_shock=-0.05),
        StressScenario(name="ALL_SYMBOLS_-10", default_shock=-0.10),
    )


@dataclass(slots=True)
class RiskMetrics:
    computed_at: datetime
    equity: float
    var_amounts: dict[str, float]
    cvar_amounts: dict[str, float]
    var_ratios: dict[str, float]
    cvar_ratios: dict[str, float]
    stress_pnls: dict[str, float]
    stress_loss_ratios: dict[str, float]
    worst_stress_name: str
    worst_stress_pnl: float
    worst_stress_loss_ratio: float
    volatility: dict[str, float]
    bars_used: int
    level: int
    level_reason: str


class RiskAnalytics:
    """Collects bars from ticks and computes portfolio risk metrics."""

    def __init__(self, config: RiskAnalyticsConfig | None = None) -> None:
        self.config = config or RiskAnalyticsConfig()

        self._close_history: dict[str, deque[float]] = {}
        self._open_bucket_start: dict[str, datetime] = {}
        self._open_bucket_close: dict[str, float] = {}

        self._latest_marks: dict[str, float] = {}
        self._latest_metrics: RiskMetrics | None = None
        self._last_compute_at: datetime | None = None

        self._risk_level: int = 0
        self._upgrade_streak: int = 0
        self._downgrade_streak: int = 0

    def on_tick(self, tick: TickData) -> None:
        """Feed real-time ticks and aggregate close prices into fixed bars."""
        if tick.last_price <= 0:
            return
        vt_symbol = tick.vt_symbol
        bar_start = _floor_to_interval(tick.datetime, self.config.bar_minutes)
        self._latest_marks[vt_symbol] = tick.last_price

        active_bucket = self._open_bucket_start.get(vt_symbol)
        if active_bucket is None:
            self._open_bucket_start[vt_symbol] = bar_start
            self._open_bucket_close[vt_symbol] = tick.last_price
            return

        if bar_start == active_bucket:
            self._open_bucket_close[vt_symbol] = tick.last_price
            return

        self._append_close(vt_symbol, self._open_bucket_close[vt_symbol])
        self._open_bucket_start[vt_symbol] = bar_start
        self._open_bucket_close[vt_symbol] = tick.last_price

    def compute_if_due(
        self,
        *,
        positions_by_vt_symbol: dict[str, float],
        equity: float,
        now: datetime | None = None,
    ) -> RiskMetrics | None:
        """Compute metrics only when recompute interval is reached."""
        current = now or datetime.utcnow()
        if self._last_compute_at is None:
            self._last_compute_at = current - timedelta(minutes=self.config.recompute_minutes)
        if current - self._last_compute_at < timedelta(minutes=self.config.recompute_minutes):
            return None
        return self.compute_metrics(
            positions_by_vt_symbol=positions_by_vt_symbol,
            equity=equity,
            now=current,
        )

    def compute_metrics(
        self,
        *,
        positions_by_vt_symbol: dict[str, float],
        equity: float,
        now: datetime | None = None,
    ) -> RiskMetrics:
        """Compute VaR/CVaR, stress, volatility and current risk level."""
        current = now or datetime.utcnow()
        self._last_compute_at = current

        marks = self._build_marks(positions_by_vt_symbol)
        portfolio_returns = self._build_portfolio_returns(positions_by_vt_symbol, marks)

        var_amounts: dict[str, float] = {}
        cvar_amounts: dict[str, float] = {}
        var_ratios: dict[str, float] = {}
        cvar_ratios: dict[str, float] = {}

        for level in self.config.confidence_levels:
            key = str(int(level * 100))
            if portfolio_returns and equity > 0:
                tail_q = _quantile(portfolio_returns, 1.0 - level)
                tail = [x for x in portfolio_returns if x <= tail_q]
                tail_mean = mean(tail) if tail else tail_q
                var_amt = max(0.0, -tail_q * equity)
                cvar_amt = max(0.0, -tail_mean * equity)
            else:
                var_amt = 0.0
                cvar_amt = 0.0
            var_amounts[key] = var_amt
            cvar_amounts[key] = cvar_amt
            if equity > 0:
                var_ratios[key] = var_amt / equity
                cvar_ratios[key] = cvar_amt / equity
            else:
                var_ratios[key] = 0.0
                cvar_ratios[key] = 0.0

        stress_pnls = self._run_stress_scenarios(positions_by_vt_symbol, marks)
        stress_loss_ratios = {
            name: (max(0.0, -pnl) / equity if equity > 0 else 0.0) for name, pnl in stress_pnls.items()
        }
        worst_stress_name = ""
        worst_stress_pnl = 0.0
        worst_stress_loss_ratio = 0.0
        if stress_pnls:
            worst_stress_name = min(stress_pnls, key=lambda k: stress_pnls[k])
            worst_stress_pnl = stress_pnls[worst_stress_name]
            worst_stress_loss_ratio = stress_loss_ratios[worst_stress_name]

        volatility = self._compute_volatility(portfolio_returns)
        level, reason = self._update_level(var_ratios, cvar_ratios, worst_stress_loss_ratio)

        metrics = RiskMetrics(
            computed_at=current,
            equity=equity,
            var_amounts=var_amounts,
            cvar_amounts=cvar_amounts,
            var_ratios=var_ratios,
            cvar_ratios=cvar_ratios,
            stress_pnls=stress_pnls,
            stress_loss_ratios=stress_loss_ratios,
            worst_stress_name=worst_stress_name,
            worst_stress_pnl=worst_stress_pnl,
            worst_stress_loss_ratio=worst_stress_loss_ratio,
            volatility=volatility,
            bars_used=len(portfolio_returns),
            level=level,
            level_reason=reason,
        )
        self._latest_metrics = metrics
        return metrics

    def latest_metrics(self) -> RiskMetrics | None:
        return self._latest_metrics

    def snapshot(self) -> dict[str, object]:
        metrics = self._latest_metrics
        return {
            "symbols": list(self._close_history.keys()),
            "bars_per_symbol": {s: len(v) for s, v in self._close_history.items()},
            "last_compute_at": self._last_compute_at.isoformat() if self._last_compute_at else None,
            "risk_level": self._risk_level,
            "latest_metrics": metrics,
        }

    def _append_close(self, vt_symbol: str, close_price: float) -> None:
        history = self._close_history.get(vt_symbol)
        if history is None:
            history = deque(maxlen=self.config.history_points + 1)
            self._close_history[vt_symbol] = history
        history.append(close_price)

    def _build_marks(self, positions_by_vt_symbol: dict[str, float]) -> dict[str, float]:
        marks: dict[str, float] = {}
        for symbol in positions_by_vt_symbol:
            price = self._latest_marks.get(symbol, 0.0)
            if price > 0:
                marks[symbol] = price
        return marks

    def _build_portfolio_returns(
        self,
        positions_by_vt_symbol: dict[str, float],
        marks: dict[str, float],
    ) -> list[float]:
        exposures: dict[str, float] = {}
        for symbol, position in positions_by_vt_symbol.items():
            mark = marks.get(symbol, 0.0)
            if mark <= 0 or abs(position) <= 0:
                continue
            exposures[symbol] = position * mark
        exposure_abs_sum = sum(abs(x) for x in exposures.values())
        if exposure_abs_sum <= 0:
            return []
        weights = {s: e / exposure_abs_sum for s, e in exposures.items()}

        symbol_returns: dict[str, list[float]] = {}
        min_len = 0
        for symbol in weights:
            closes = list(self._close_history.get(symbol, []))
            if len(closes) < 2:
                continue
            rets = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
            if not rets:
                continue
            symbol_returns[symbol] = rets
            min_len = len(rets) if min_len == 0 else min(min_len, len(rets))

        if min_len == 0:
            return []

        portfolio_returns: list[float] = []
        for idx in range(1, min_len + 1):
            rp = 0.0
            for symbol, w in weights.items():
                rp += w * symbol_returns[symbol][-idx]
            portfolio_returns.append(rp)
        portfolio_returns.reverse()
        return portfolio_returns

    def _run_stress_scenarios(
        self,
        positions_by_vt_symbol: dict[str, float],
        marks: dict[str, float],
    ) -> dict[str, float]:
        results: dict[str, float] = {}
        for scenario in self.config.stress_scenarios:
            pnl = 0.0
            for symbol, position in positions_by_vt_symbol.items():
                mark = marks.get(symbol, 0.0)
                if mark <= 0 or abs(position) <= 0:
                    continue
                shock = scenario.per_symbol_shock.get(symbol, scenario.default_shock)
                pnl += position * mark * shock
            results[scenario.name] = pnl
        return results

    def _compute_volatility(self, portfolio_returns: list[float]) -> dict[str, float]:
        result: dict[str, float] = {}
        if len(portfolio_returns) < 2:
            for bars in self.config.volatility_windows_bars:
                key = f"rv_{bars}_bars"
                result[key] = 0.0
            return result

        bars_per_day = int((24 * 60) / self.config.bar_minutes)
        annual_factor = sqrt(365 * bars_per_day)
        for bars in self.config.volatility_windows_bars:
            key = f"rv_{bars}_bars"
            sample = portfolio_returns[-bars:] if len(portfolio_returns) >= bars else portfolio_returns
            if len(sample) < 2:
                result[key] = 0.0
                continue
            result[key] = pstdev(sample) * annual_factor
        return result

    def _update_level(
        self,
        var_ratios: dict[str, float],
        cvar_ratios: dict[str, float],
        worst_stress_ratio: float,
    ) -> tuple[int, str]:
        var95 = var_ratios.get("95", 0.0)
        cvar95 = cvar_ratios.get("95", 0.0)

        target = 0
        reason = "NORMAL"
        if worst_stress_ratio > self.config.level3_stress_ratio:
            target = 3
            reason = "STRESS_LEVEL3"
        elif cvar95 > self.config.level2_cvar_ratio:
            target = 2
            reason = "CVAR_LEVEL2"
        elif var95 > self.config.level1_var_ratio:
            target = 1
            reason = "VAR_LEVEL1"

        if target > self._risk_level:
            self._upgrade_streak += 1
            self._downgrade_streak = 0
            if self._upgrade_streak >= self.config.upgrade_confirmations:
                self._risk_level = target
                self._upgrade_streak = 0
                return self._risk_level, reason
            return self._risk_level, f"PENDING_UPGRADE:{reason}"

        if target < self._risk_level:
            should_downgrade = self._below_recovery_threshold(var95, cvar95, worst_stress_ratio)
            if should_downgrade:
                self._downgrade_streak += 1
                self._upgrade_streak = 0
                if self._downgrade_streak >= self.config.downgrade_confirmations:
                    self._risk_level = target
                    self._downgrade_streak = 0
                    return self._risk_level, "RECOVERED"
                return self._risk_level, "PENDING_DOWNGRADE"
            self._downgrade_streak = 0
            return self._risk_level, "HOLD_LEVEL"

        self._upgrade_streak = 0
        self._downgrade_streak = 0
        return self._risk_level, reason

    def _below_recovery_threshold(self, var95: float, cvar95: float, stress: float) -> bool:
        h = self.config.hysteresis_ratio
        if self._risk_level >= 3:
            return stress <= self.config.level3_stress_ratio * h
        if self._risk_level == 2:
            return cvar95 <= self.config.level2_cvar_ratio * h
        if self._risk_level == 1:
            return var95 <= self.config.level1_var_ratio * h
        return True
