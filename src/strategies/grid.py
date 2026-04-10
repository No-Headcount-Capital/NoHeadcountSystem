from src.infrastructure.models import Direction, Offset, OrderData, TickData, TradeData
from src.strategies.base import BaseStrategy


class GridStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_name,
        event_engine,
        vt_symbol,
        lower_price: float,
        upper_price: float,
        grid_volume: float,
    ) -> None:
        super().__init__(strategy_name=strategy_name, event_engine=event_engine, vt_symbol=vt_symbol)
        self.lower_price = lower_price
        self.upper_price = upper_price
        self.grid_volume = grid_volume
        self.last_action = ""

    def on_tick(self, tick: TickData) -> None:
        if tick.last_price <= self.lower_price and self.last_action != "buy":
            self.emit_signal(
                direction=Direction.LONG,
                price=tick.ask_price_1,
                volume=self.grid_volume,
                offset=Offset.OPEN,
            )
            self.last_action = "buy"
        elif tick.last_price >= self.upper_price and self.last_action != "sell":
            self.emit_signal(
                direction=Direction.SHORT,
                price=tick.bid_price_1,
                volume=self.grid_volume,
                offset=Offset.CLOSE,
            )
            self.last_action = "sell"

    def on_order(self, order: OrderData) -> None:
        print(f"[GRID_ORDER] id={order.order_id} status={order.status.value} price={order.price} volume={order.volume}")

    def on_trade(self, trade: TradeData) -> None:
        print(f"[GRID_TRADE] trade_id={trade.trade_id} price={trade.price} volume={trade.volume}")
