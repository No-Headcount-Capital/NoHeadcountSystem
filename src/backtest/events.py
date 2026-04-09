from numba import float64, types
from numba.experimental import jitclass

trade_spec = [("price", float64), ("quantity", float64), ("side", float64)]

orderbook_spec = [
    ("bids", float64[:, :]),
    ("asks", float64[:, :]),
]

order_spec = [
    ("state", types.unicode_type),
    ("side", types.unicode_type),
    ("type", types.unicode_type),
    ("id", float64),
    ("price", float64),
    ("quantity", float64),
]

kline_spec = [
    ("open", float64),
    ("high", float64),
    ("low", float64),
    ("close", float64),
    ("signal", float64),
    ("signal_flag", float64),
    ("extras", float64[:]),
]

open_signal_spec = [
    ("open", float64),
    ("signal", float64),
    ("extras", float64[:]),
]


@jitclass(open_signal_spec)
class OpenSignal:
    def __init__(self, open, signal, extras):
        self.open = open
        self.signal = signal
        self.extras = extras


@jitclass(kline_spec)
class Kline:
    def __init__(self, open, high, low, close, signal, signal_flag, extras):
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.signal = signal
        self.signal_flag = signal_flag
        self.extras = extras

    @property
    def is_signal_flag(self):
        return self.signal_flag == 1.0


@jitclass(trade_spec)
class Trade:
    def __init__(self, price, quantity, side):
        self.price = price
        self.quantity = quantity
        self.side = side

    @property
    def is_buy(self):
        return self.side == 0

    @property
    def notional(self):
        return self.price * self.quantity


@jitclass(orderbook_spec)
class Orderbook:
    def __init__(self, bids, asks):
        self.bids = bids
        self.asks = asks

    def get_best_bid(self):
        if len(self.bids) == 0:
            return None
        return self.bids[0]

    def get_best_ask(self):
        if len(self.asks) == 0:
            return None
        return self.asks[0]

    def get_bids(self):
        return self.bids

    def get_asks(self):
        return self.asks


@jitclass(order_spec)
class Order:
    def __init__(self, price, quantity, id, side, type="LIMIT"):
        self.price = price
        self.quantity = quantity
        self.id = id
        self.side = side
        self.state = "NEW"
        self.type = type

    def set_state(self, state):
        self.state = state
        return self
