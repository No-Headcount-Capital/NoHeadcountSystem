from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


class Exchange(str, Enum):
    BINANCE = "BINANCE"
    SIMULATED = "SIMULATED"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Offset(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class TickData:
    symbol: str
    exchange: Exchange
    datetime: datetime
    last_price: float
    bid_price_1: float
    ask_price_1: float
    bid_volume_1: float = 0.0
    ask_volume_1: float = 0.0
    volume: float = 0.0
    vt_symbol: str = field(init=False)

    def __post_init__(self) -> None:
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"


@dataclass(slots=True)
class Signal:
    strategy_name: str
    symbol: str
    exchange: Exchange
    direction: Direction
    offset: Offset
    order_type: OrderType
    price: float
    volume: float
    signal_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=datetime.utcnow)
    vt_symbol: str = field(init=False)

    def __post_init__(self) -> None:
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"


@dataclass(slots=True)
class OrderData:
    symbol: str
    exchange: Exchange
    direction: Direction
    offset: Offset
    order_type: OrderType
    price: float
    volume: float
    strategy_name: str
    signal_id: str
    order_id: str = field(default_factory=lambda: f"LOCAL_{uuid4().hex[:10]}")
    traded_volume: float = 0.0
    status: OrderStatus = OrderStatus.SUBMITTING
    reject_reason: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    exchange_order_id: str = ""
    vt_symbol: str = field(init=False)
    vt_order_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"
        self.vt_order_id = f"{self.exchange.value}.{self.order_id}"


@dataclass(slots=True)
class TradeData:
    order_id: str
    symbol: str
    exchange: Exchange
    direction: Direction
    offset: Offset
    price: float
    volume: float
    strategy_name: str
    signal_id: str
    trade_id: str = field(default_factory=lambda: f"TRADE_{uuid4().hex[:10]}")
    traded_at: datetime = field(default_factory=datetime.utcnow)
    vt_symbol: str = field(init=False)
    vt_order_id: str = field(init=False)
    vt_trade_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"
        self.vt_order_id = f"{self.exchange.value}.{self.order_id}"
        self.vt_trade_id = f"{self.exchange.value}.{self.trade_id}"
