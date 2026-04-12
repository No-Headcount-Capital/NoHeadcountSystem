"""Risk policy definitions and reject code constants.

This module intentionally contains only rule configuration and shared
reject code definitions. It does not depend on runtime engines.
"""

from dataclasses import dataclass


class RiskRejectCode:
    """Stable reject reason codes for OMS, logs, and dashboards."""

    KILL_SWITCH_ACTIVE = "RISK_KILL_SWITCH_ACTIVE"
    STRATEGY_IN_COOLDOWN = "RISK_STRATEGY_IN_COOLDOWN"
    INVALID_VOLUME = "RISK_INVALID_VOLUME"
    INVALID_LIMIT_PRICE = "RISK_INVALID_LIMIT_PRICE"
    MAX_ORDER_VOLUME = "RISK_MAX_ORDER_VOLUME"
    MAX_STRATEGY_ORDER_VOLUME = "RISK_MAX_STRATEGY_ORDER_VOLUME"
    CLOSE_WITHOUT_POSITION = "RISK_CLOSE_WITHOUT_POSITION"
    CLOSE_VOLUME_EXCEEDS_POSITION = "RISK_CLOSE_VOLUME_EXCEEDS_POSITION"
    MAX_SYMBOL_POSITION = "RISK_MAX_SYMBOL_POSITION"
    MAX_STRATEGY_POSITION = "RISK_MAX_STRATEGY_POSITION"
    MISSING_REFERENCE_PRICE = "RISK_MISSING_REFERENCE_PRICE"
    MAX_ORDER_NOTIONAL = "RISK_MAX_ORDER_NOTIONAL"
    MAX_TOTAL_NOTIONAL = "RISK_MAX_TOTAL_NOTIONAL"
    MAX_ORDERS_PER_MINUTE = "RISK_MAX_ORDERS_PER_MINUTE"
    MAX_DRAWDOWN = "RISK_MAX_DRAWDOWN"


@dataclass(slots=True)
class RiskPolicy:
    """Rule parameters shared by runtime and backtest risk managers."""

    # Position and size controls
    max_order_volume: float = 1.0
    max_symbol_position: float = 2.0
    max_strategy_order_volume: float = 1.0
    max_strategy_position: float = 2.0

    # Exposure controls
    max_notional_per_order: float = 0.0  # 0 means disabled
    max_total_notional: float = 0.0  # 0 means disabled

    # Frequency and resilience controls
    max_orders_per_minute: int = 0  # 0 means disabled
    max_consecutive_rejections: int = 0  # 0 means disabled
    cooldown_seconds: float = 60.0

    # Kill switch and drawdown controls
    enable_kill_switch: bool = True
    enable_auto_kill_switch: bool = False
    max_drawdown: float = 0.0  # 0 means disabled
    initial_equity: float = 0.0  # 0 means unknown until first mark
