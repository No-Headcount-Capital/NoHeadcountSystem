from abc import ABC, abstractmethod

from src.infrastructure.event_engine import EventEngine
from src.infrastructure.events import EVENT_ORDER, EVENT_SIGNAL, EVENT_TICK, EVENT_TRADE, Event
from src.infrastructure.models import Direction, Exchange, Offset, OrderData, OrderType, Signal, TickData, TradeData


class BaseStrategy(ABC):
    def __init__(self, strategy_name: str, event_engine: EventEngine, vt_symbol: str) -> None:
        self.strategy_name = strategy_name
        self.event_engine = event_engine
        self.vt_symbol = vt_symbol
        self.active = False
        self.event_engine.register(EVENT_TICK, self._on_tick_event)
        self.event_engine.register(EVENT_ORDER, self._on_order_event)
        self.event_engine.register(EVENT_TRADE, self._on_trade_event)

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def _on_tick_event(self, event: Event) -> None:
        if not self.active:
            return
        tick: TickData = event.data
        if tick.vt_symbol != self.vt_symbol:
            return
        self.on_tick(tick)

    def _on_order_event(self, event: Event) -> None:
        if not self.active:
            return
        order: OrderData = event.data
        if order.strategy_name != self.strategy_name:
            return
        self.on_order(order)

    def _on_trade_event(self, event: Event) -> None:
        if not self.active:
            return
        trade: TradeData = event.data
        if trade.strategy_name != self.strategy_name:
            return
        self.on_trade(trade)

    def emit_signal(
        self,
        direction: Direction,
        price: float,
        volume: float,
        offset: Offset = Offset.OPEN,
        order_type: OrderType = OrderType.LIMIT,
    ) -> Signal:
        symbol, exchange = self.vt_symbol.split(".")
        signal = Signal(
            strategy_name=self.strategy_name,
            symbol=symbol,
            exchange=Exchange(exchange),
            direction=direction,
            offset=offset,
            order_type=order_type,
            price=price,
            volume=volume,
        )
        self.event_engine.put(Event(EVENT_SIGNAL, signal))
        return signal

    @abstractmethod
    def on_tick(self, tick: TickData) -> None:
        raise NotImplementedError

    def on_order(self, order: OrderData) -> None:
        return

    def on_trade(self, trade: TradeData) -> None:
        return
