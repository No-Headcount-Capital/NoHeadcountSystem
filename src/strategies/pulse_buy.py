import time

from src.infrastructure.models import Direction, Offset, OrderData, OrderType, TickData, TradeData
from src.strategies.base import BaseStrategy


class PulseBuyStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_name: str,
        event_engine,
        vt_symbol: str,
        interval_seconds: float,
        buy_volume: float,
    ) -> None:
        super().__init__(strategy_name=strategy_name, event_engine=event_engine, vt_symbol=vt_symbol)
        self.interval_seconds = interval_seconds
        self.buy_volume = buy_volume
        self._last_emit_ts = 0.0

    def on_tick(self, tick: TickData) -> None:
        now = time.monotonic()
        if now - self._last_emit_ts < self.interval_seconds:
            return
        aggressive_price = round(max(tick.ask_price_1, tick.last_price) * 1.02, 2)
        self.emit_signal(
            direction=Direction.LONG,
            price=aggressive_price,
            volume=self.buy_volume,
            offset=Offset.OPEN,
            order_type=OrderType.LIMIT,
        )
        self._last_emit_ts = now

    def on_order(self, order: OrderData) -> None:
        print(
            f"[PULSE_ORDER] id={order.order_id} status={order.status.value} "
            f"price={order.price} volume={order.volume} reason={order.reject_reason}"
        )

    def on_trade(self, trade: TradeData) -> None:
        print(f"[PULSE_TRADE] trade_id={trade.trade_id} price={trade.price} volume={trade.volume}")
