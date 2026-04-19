import logging
import os
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import Event as StopEvent
from threading import Thread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.event_engine import EventEngine
from src.infrastructure.events import EVENT_ORDER, EVENT_TICK, EVENT_TRADE, Event
from src.infrastructure.gateway import BinanceTestnetGateway, SimulatedGateway
from src.infrastructure.oms import OrderManager
from src.risk.analytics import RiskAnalytics, RiskAnalyticsConfig
from src.risk.policy import RiskPolicy
from src.risk.runtime_manager import RuntimeRiskManager
from src.strategies.cep import CEPEngine
from src.strategies.dca import FeeAwareStatArbStrategy
from src.strategies.grid import GridStrategy
from src.strategies.pulse_buy import PulseBuyStrategy

logger = logging.getLogger(__name__)


def setup_logging():
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "trading.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清除默认的 handlers 防止重复打印
    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = TimedRotatingFileHandler(
        filename=log_file, when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None
    env_paths = [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / ".env.local",
        PROJECT_ROOT / "algo_trading_system" / ".env",
        PROJECT_ROOT / "algo_trading_system" / ".env.local",
    ]
    loaded = False
    for env_path in env_paths:
        if not env_path.exists():
            continue
        if load_dotenv:
            load_dotenv(dotenv_path=env_path, override=False)
            loaded = True
            continue
        text = env_path.read_text(encoding="utf-8")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value
        loaded = True
    if not loaded:
        logger.info("未找到 .env 文件，将使用系统环境变量")


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def resolve_mode() -> str:
    mode = os.getenv("TRADING_MODE", "").strip().upper()
    if mode:
        return mode
    api_key = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()
    if api_key and api_secret:
        return "BINANCE"
    return "SIM"


def build_system() -> tuple[
    EventEngine, SimulatedGateway | BinanceTestnetGateway, CEPEngine
]:
    event_engine = EventEngine()
    mode = resolve_mode()
    if mode == "BINANCE":
        gateway = BinanceTestnetGateway(event_engine)
    else:
        gateway = SimulatedGateway(event_engine)
    risk_policy = RiskPolicy(
        max_order_volume=env_float("RISK_MAX_ORDER_VOLUME", 0.01),
        max_symbol_position=env_float("RISK_MAX_SYMBOL_POSITION", 0.05),
        max_strategy_order_volume=env_float("RISK_MAX_STRATEGY_ORDER_VOLUME", 0.01),
        max_strategy_position=env_float("RISK_MAX_STRATEGY_POSITION", 0.05),
        max_notional_per_order=env_float("RISK_MAX_NOTIONAL_PER_ORDER", 0.0),
        max_total_notional=env_float("RISK_MAX_TOTAL_NOTIONAL", 0.0),
        max_orders_per_minute=env_int("RISK_MAX_ORDERS_PER_MINUTE", 0),
        max_consecutive_rejections=env_int("RISK_MAX_CONSECUTIVE_REJECTIONS", 0),
        cooldown_seconds=env_float("RISK_COOLDOWN_SECONDS", 60.0),
        enable_kill_switch=bool_env("RISK_ENABLE_KILL_SWITCH", True),
        enable_auto_kill_switch=bool_env("RISK_ENABLE_AUTO_KILL_SWITCH", False),
        max_drawdown=env_float("RISK_MAX_DRAWDOWN", 0.0),
        initial_equity=env_float("RISK_INITIAL_EQUITY", 0.0),
    )
    risk_manager = RuntimeRiskManager(
        policy=risk_policy,
        analytics_level2_scale=env_float("RISK_LEVEL2_SCALE", 0.5),
    )
    OrderManager(event_engine=event_engine, gateway=gateway, risk_manager=risk_manager)
    register_risk_analytics(event_engine=event_engine, risk_manager=risk_manager)
    cep_engine = CEPEngine()
    return event_engine, gateway, cep_engine


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def register_log_handlers(event_engine: EventEngine) -> None:
    show_tick = os.getenv("LOG_TICK", "1").strip() not in {"0", "false", "False"}

    def on_tick(event: Event) -> None:
        if not show_tick:
            return
        tick = event.data
        logger.info(
            f"[TICK] symbol={tick.symbol} last={tick.last_price} "
            f"bid1={tick.bid_price_1} ask1={tick.ask_price_1} time={tick.datetime.isoformat()}"
        )

    def on_order(event: Event) -> None:
        order = event.data
        logger.info(
            f"[ORDER] strategy={order.strategy_name} id={order.order_id} status={order.status.value} "
            f"symbol={order.symbol} price={order.price} volume={order.volume} reject_reason={order.reject_reason}"
        )

    def on_trade(event: Event) -> None:
        trade = event.data
        logger.info(
            f"[TRADE] strategy={trade.strategy_name} trade_id={trade.trade_id} "
            f"symbol={trade.symbol} price={trade.price} volume={trade.volume}"
        )

    event_engine.register(EVENT_TICK, on_tick)
    event_engine.register(EVENT_ORDER, on_order)
    event_engine.register(EVENT_TRADE, on_trade)


def register_risk_analytics(
    event_engine: EventEngine, risk_manager: RuntimeRiskManager
) -> None:
    enabled = bool_env("RISK_ANALYTICS_ENABLE", True)
    if not enabled:
        return
    analytics = RiskAnalytics(
        RiskAnalyticsConfig(
            bar_minutes=env_int("RISK_ANALYTICS_BAR_MINUTES", 10),
            recompute_minutes=env_int("RISK_ANALYTICS_RECOMPUTE_MINUTES", 10),
            history_points=env_int("RISK_ANALYTICS_HISTORY_POINTS", 2880),
            level1_var_ratio=env_float("RISK_ANALYTICS_LEVEL1_VAR_RATIO", 0.03),
            level2_cvar_ratio=env_float("RISK_ANALYTICS_LEVEL2_CVAR_RATIO", 0.05),
            level3_stress_ratio=env_float("RISK_ANALYTICS_LEVEL3_STRESS_RATIO", 0.10),
            upgrade_confirmations=env_int("RISK_ANALYTICS_UPGRADE_CONFIRMATIONS", 3),
            downgrade_confirmations=env_int(
                "RISK_ANALYTICS_DOWNGRADE_CONFIRMATIONS", 3
            ),
            hysteresis_ratio=env_float("RISK_ANALYTICS_HYSTERESIS_RATIO", 0.8),
        )
    )
    fallback_equity = env_float("RISK_ANALYTICS_EQUITY_FALLBACK", 10000.0)
    if risk_manager.get_equity() <= 0 and fallback_equity > 0:
        risk_manager.mark_equity(fallback_equity)

    def on_tick_for_analytics(event: Event) -> None:
        tick = event.data
        analytics.on_tick(tick)
        equity = risk_manager.get_equity()
        metrics = analytics.compute_if_due(
            positions_by_vt_symbol=risk_manager.get_symbol_positions(),
            equity=equity,
        )
        if not metrics:
            return
        risk_manager.apply_analytics_metrics(metrics)
        var95 = metrics.var_ratios.get("95", 0.0)
        cvar95 = metrics.cvar_ratios.get("95", 0.0)
        rv_24h = metrics.volatility.get("rv_144_bars", 0.0)
        logger.info(
            "[RISK_ANALYTICS] "
            f"level={metrics.level} reason={metrics.level_reason} "
            f"var95={var95:.4f} cvar95={cvar95:.4f} "
            f"stress={metrics.worst_stress_name}:{metrics.worst_stress_loss_ratio:.4f} "
            f"rv24h={rv_24h:.4f} bars={metrics.bars_used}"
        )

    event_engine.register(EVENT_TICK, on_tick_for_analytics)


def start_simulated_stream(
    gateway: SimulatedGateway, symbol: str
) -> tuple[Thread, StopEvent]:
    stop_signal = StopEvent()
    prices = [66720, 66700, 66680, 66660, 66640, 66600, 66580, 66650, 66740, 66820]

    def loop() -> None:
        idx = 0
        while not stop_signal.is_set():
            gateway.publish_tick(symbol=symbol, last_price=prices[idx % len(prices)])
            idx += 1
            time.sleep(0.6)

    thread = Thread(target=loop, daemon=True)
    thread.start()
    return thread, stop_signal


def build_strategy(event_engine: EventEngine, symbol: str, exchange_name: str):
    vt_symbol = f"{symbol}.{exchange_name}"
    strategy_type = os.getenv("STRATEGY_TYPE", "GRID").strip().upper()
    if strategy_type == "PULSE_BUY":
        return PulseBuyStrategy(
            strategy_name=os.getenv("PULSE_STRATEGY_NAME", "pulse_buy_v1"),
            event_engine=event_engine,
            vt_symbol=vt_symbol,
            interval_seconds=env_float("PULSE_INTERVAL_SECONDS", 3.0),
            buy_volume=env_float("PULSE_BUY_VOLUME", 0.0001),
        )
    elif strategy_type == "DCA":
        return FeeAwareStatArbStrategy(
            strategy_name=os.getenv("DCA_STRATEGY_NAME", "dca_v1"),
            event_engine=event_engine,
            vt_symbol=vt_symbol,
            ema_alpha=env_float("DCA_EMA_ALPHA", 0.005),
            taker_fee=env_float("DCA_TAKER_FEE", 0.0004),
            target_profit_rate=env_float("DCA_TARGET_PROFIT_RATE", 0.0002),
            dca_step_rate=env_float("DCA_STEP_RATE", 0.0015),
            stop_loss_rate=env_float("DCA_STOP_LOSS_RATE", 0.02),
            trade_volume=env_float("DCA_TRADE_VOLUME", 1.0),
            max_position=env_float("DCA_MAX_POSITION", 5.0),
        )
    return GridStrategy(
        strategy_name=os.getenv("GRID_STRATEGY_NAME", "grid_v1"),
        event_engine=event_engine,
        vt_symbol=vt_symbol,
        lower_price=env_float("GRID_LOWER_PRICE", 66000),
        upper_price=env_float("GRID_UPPER_PRICE", 67000),
        grid_volume=env_float("GRID_VOLUME", 0.005),
    )


def main() -> None:
    setup_logging()
    load_env_file()
    event_engine, gateway, cep_engine = build_system()
    logger.info(f"当前运行模式: {gateway.exchange.value}")
    register_log_handlers(event_engine)
    event_engine.start()
    try:
        gateway.connect()
    except Exception as exc:
        event_engine.stop()
        raise RuntimeError(f"连接网关失败: {exc}") from exc
    symbol = os.getenv("TRADING_SYMBOL", "BTCUSDT")
    gateway.subscribe(symbol)
    strategy = build_strategy(
        event_engine=event_engine, symbol=symbol, exchange_name=gateway.exchange.value
    )
    logger.info(f"当前策略: {strategy.strategy_name}")
    cep_engine.add_strategy(strategy)
    cep_engine.start()
    use_gui = bool_env("ENABLE_GUI", True)
    sim_thread: Thread | None = None
    sim_stop: StopEvent | None = None
    if isinstance(gateway, SimulatedGateway):
        sim_thread, sim_stop = start_simulated_stream(gateway, symbol)

    try:
        if use_gui:
            from src.dashboard.order_monitor import OrderMonitorGUI

            gui = OrderMonitorGUI(event_engine=event_engine)
            gui.run()
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if sim_stop:
            sim_stop.set()
        if sim_thread and sim_thread.is_alive():
            sim_thread.join(timeout=2)
        cep_engine.stop()
        gateway.close()
        event_engine.stop()


if __name__ == "__main__":
    main()
