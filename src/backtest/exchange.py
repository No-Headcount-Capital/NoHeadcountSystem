import numpy as np
from numba import float64, int64, typed, types
from numba.experimental import jitclass

from .events import Order

kline_exchange_spec = [
    ("maker_commission", float64),
    ("taker_commission", float64),
    ("balance", float64),
    ("position", float64),
    ("realized_total_pnl", float64),
    ("unrealized_pnl", float64),
    ("turnover", float64),
    ("realized_trade_pnls", types.ListType(float64)),
    ("cost_price", float64),
    ("price_tick", float64),
    ("orders", float64[:, :]),
    ("order_id_index_map", float64[:]),
    ("max_orders", int64),
    ("reference_price", float64),
    ("order_id", float64),
]


@jitclass(kline_exchange_spec)
class KlineExchange:
    def __init__(self, maker_commission, taker_commission, balance, price_tick, max_orders):
        self.maker_commission = maker_commission
        self.taker_commission = taker_commission
        self.balance = balance
        self.position = 0.0
        self.cost_price = 0.0
        self.turnover = 0.0
        self.price_tick = price_tick
        self.max_orders = max_orders
        self.orders = np.zeros((max_orders, 3), dtype=np.float64)
        self.order_id_index_map = np.full(max_orders, -1, dtype=np.float64)
        self.realized_total_pnl = 0.0
        self.realized_trade_pnls = typed.List.empty_list(float64)
        self.reference_price = 0.0
        self.unrealized_pnl = 0.0
        self.order_id = 1.0

    def _get_index_by_order_id(self, order_id):
        for i in range(self.max_orders):
            if self.order_id_index_map[i] == order_id:
                return i
        return -1

    def _get_spare_order_index(self):
        for i in range(self.max_orders):
            if self.order_id_index_map[i] == -1:
                return i
        return -1

    def round_price(self, price):
        return round(price / self.price_tick) * self.price_tick

    def _update_trade(self, price, trade_size):
        new_size = trade_size + self.position
        if abs(self.position) < 1e-9:
            self.cost_price = price
        elif self.position * trade_size > 0:
            total_cost = self.cost_price * abs(self.position) + price * abs(trade_size)
            self.cost_price = total_cost / abs(new_size)
        else:
            if abs(new_size) < 1e-9:
                realized_pnl = (price - self.cost_price) * self.position
                self.realized_total_pnl += realized_pnl
                self.realized_trade_pnls.append(realized_pnl)
                self.cost_price = 0.0
            else:
                close_size = min(abs(self.position), abs(trade_size))
                realized_pnl = (price - self.cost_price) * close_size
                if self.position < 0:
                    realized_pnl = -realized_pnl

                self.realized_total_pnl += realized_pnl
                self.realized_trade_pnls.append(realized_pnl)

                if new_size * self.position < 0:
                    self.cost_price = price
        self.position = new_size
        self.turnover += abs(price * trade_size)

    def cancel_order(self, order_id):
        index = self._get_index_by_order_id(order_id)
        if index != -1:
            self.order_id_index_map[index] = -1
            self.orders[index] = np.array([0.0, 0.0, 0.0], dtype=np.float64)

    def cancel_all_order(self):
        self.orders = np.zeros((self.max_orders, 3), dtype=np.float64)
        self.order_id_index_map = np.full(self.max_orders, -1, dtype=np.float64)

    def place_market_order(self, quantity):
        self._update_trade(self.reference_price, quantity)
        self.realized_total_pnl -= abs(quantity * self.reference_price) * self.taker_commission
        return -1

    def place_limit_order(self, price, quantity):
        if quantity == 0:
            return False
        if quantity > 0 and price > self.reference_price:
            self.place_market_order(quantity)
            return -1
        if quantity < 0 and price < self.reference_price:
            self.place_market_order(quantity)
            return -1

        index = self._get_spare_order_index()
        if index == -1:
            return False

        order_id = self.order_id
        self.orders[index] = np.array([price, quantity, order_id], dtype=np.float64)
        self.order_id_index_map[index] = order_id
        self.order_id += 1.0
        return order_id

    def set_reference_price(self, price):
        self.reference_price = price

    def _match_single_limit_order(self, index, price):
        order_price = self.orders[index][0]
        quantity = self.orders[index][1]
        if quantity > 0:
            return price <= order_price
        return price >= order_price

    def match_limit_order(self, price):
        flag = False
        filled_order = None
        for index in range(self.max_orders):
            if self.order_id_index_map[index] != -1:
                if self._match_single_limit_order(index, price):
                    order_price = self.orders[index][0]
                    order_size = self.orders[index][1]
                    order_id = self.orders[index][2]
                    self._update_trade(order_price, order_size)
                    self.realized_total_pnl -= abs(order_price * order_size) * self.maker_commission
                    self.order_id_index_map[index] = -1
                    self.orders[index] = np.array([0.0, 0.0, 0.0], dtype=np.float64)
                    if order_size > 0:
                        filled_order = Order(order_price, order_size, order_id, "BUY", "LIMIT").set_state("FINISHED")
                    else:
                        filled_order = Order(order_price, order_size, order_id, "SELL", "LIMIT").set_state("FINISHED")
                    flag = True
        if flag:
            return filled_order
        return None

    def get_unrealized_pnl(self, price):
        return (price - self.cost_price) * self.position

    def query_order(self, order_id):
        return self._get_index_by_order_id(order_id)
