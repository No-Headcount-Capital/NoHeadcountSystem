from src.infrastructure.event_engine import EventEngine
from src.infrastructure.events import EVENT_ORDER, EVENT_SIGNAL, EVENT_TRADE, Event
from src.infrastructure.gateway import BaseGateway
from src.infrastructure.models import OrderData, OrderStatus, Signal, TradeData
from src.risk.manager import RiskManager


class OrderManager:
    def __init__(self, event_engine: EventEngine, gateway: BaseGateway, risk_manager: RiskManager) -> None:
        self.event_engine = event_engine
        self.gateway = gateway
        self.risk_manager = risk_manager
        self.orders: dict[str, OrderData] = {}
        self.signal_to_order: dict[str, str] = {}
        self.event_engine.register(EVENT_SIGNAL, self.on_signal)
        self.event_engine.register(EVENT_ORDER, self.on_order_event)
        self.event_engine.register(EVENT_TRADE, self.on_trade_event)

    def on_signal(self, event: Event) -> None:
        signal: Signal = event.data
        passed, _ = self.risk_manager.validate_signal(signal)
        order = OrderData(
            symbol=signal.symbol,
            exchange=signal.exchange,
            direction=signal.direction,
            offset=signal.offset,
            order_type=signal.order_type,
            price=signal.price,
            volume=signal.volume,
            strategy_name=signal.strategy_name,
            signal_id=signal.signal_id,
        )
        if not passed:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "risk_manager_rejected"
            self.orders[order.order_id] = order
            self.signal_to_order[signal.signal_id] = order.order_id
            self.event_engine.put(Event(EVENT_ORDER, order))
            return
        self.orders[order.order_id] = order
        self.signal_to_order[signal.signal_id] = order.order_id
        self.gateway.send_order(order)

    def on_order_event(self, event: Event) -> None:
        order: OrderData = event.data
        if order.order_id in self.orders:
            self.orders[order.order_id] = order

    def on_trade_event(self, event: Event) -> None:
        trade: TradeData = event.data
        order = self.orders.get(trade.order_id)
        if order:
            order.traded_volume += trade.volume
            if order.traded_volume >= order.volume:
                order.status = OrderStatus.FILLED
        self.risk_manager.on_order_filled(trade.vt_symbol, trade.direction, trade.volume)
