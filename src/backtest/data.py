import numpy as np
from numba import njit, types
from numba.experimental import jitclass
from numba.typed import Dict, List

LEVEL = 20


@njit
def numba_bisect_left(a, x):
    lo = 0
    hi = len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit
def numba_insort(a, x):
    idx = numba_bisect_left(a, x)
    a.insert(idx, x)


orderbook_spec = [
    ("bids", types.DictType(types.float64, types.float64)),
    ("asks", types.DictType(types.float64, types.float64)),
    ("_sorted_bids", types.ListType(types.float64)),
    ("_sorted_asks", types.ListType(types.float64)),
]

orderbook_dt = np.dtype(
    [
        ("timestamp", np.int64),
        ("bid", np.float64, (100, 2)),
        ("ask", np.float64, (100, 2)),
    ]
)


@jitclass(orderbook_spec)
class NumbaOrderBook:
    def __init__(self):
        self.bids = Dict.empty(key_type=types.float64, value_type=types.float64)
        self.asks = Dict.empty(key_type=types.float64, value_type=types.float64)
        self._sorted_bids = List.empty_list(types.float64)
        self._sorted_asks = List.empty_list(types.float64)

    def init_prices(self, orders, is_bid):
        prices = List.empty_list(types.float64)
        quantities = List.empty_list(types.float64)
        for price, qty in orders:
            prices.append(price)
            quantities.append(qty)
        self._update_side(prices, quantities, is_bid)

    def update_prices(self, orders, is_bid):
        prices = List.empty_list(types.float64)
        quantities = List.empty_list(types.float64)
        for price, qty in orders:
            prices.append(price)
            quantities.append(qty)
        self._update_side(prices, quantities, is_bid)

    def _update_side(self, prices, quantities, is_bid):
        target_dict = self.bids if is_bid else self.asks
        sorted_list = self._sorted_bids if is_bid else self._sorted_asks

        for price, qty in zip(prices, quantities):
            if qty == 0:
                if price in target_dict:
                    del target_dict[price]
                    idx = numba_bisect_left(sorted_list, price)
                    if idx < len(sorted_list) and sorted_list[idx] == price:
                        sorted_list.pop(idx)
            else:
                target_dict[price] = qty
                idx = numba_bisect_left(sorted_list, price)
                if idx >= len(sorted_list) or sorted_list[idx] != price:
                    numba_insort(sorted_list, price)

    def get_top_levels(self, side, n=100):
        sorted_prices = self._sorted_bids if side == "bid" else self._sorted_asks
        target_dict = self.bids if side == "bid" else self.asks

        prices = np.zeros(n, dtype=np.float64)
        quantities = np.zeros(n, dtype=np.float64)

        if side == "bid":
            for i in range(min(n, len(sorted_prices))):
                price = sorted_prices[-1 - i]
                prices[i] = price
                quantities[i] = target_dict[price]
        else:
            for i in range(min(n, len(sorted_prices))):
                price = sorted_prices[i]
                prices[i] = price
                quantities[i] = target_dict[price]

        return prices, quantities


class BybitOrderBook:
    def __init__(self):
        self.core = NumbaOrderBook()

    def init_prices(self, orders, is_bid):
        typed_orders = List()
        for price, qty in orders:
            typed_orders.append((float(price), float(qty)))
        self.core.init_prices(typed_orders, is_bid)

    def update_prices(self, orders, is_bid):
        typed_orders = List()
        for price, qty in orders:
            typed_orders.append((float(price), float(qty)))
        self.core.update_prices(typed_orders, is_bid)

    def update_string_prices(self, orders, is_bid):
        if len(orders) == 0:
            return
        typed_orders = List()
        for price_str, qty_str in orders:
            typed_orders.append((float(price_str), float(qty_str)))
        self.core.update_prices(typed_orders, is_bid)

    def get_bids(self):
        prices, quantities = self.core.get_top_levels("bid", LEVEL)
        return prices, quantities

    def get_asks(self):
        prices, quantities = self.core.get_top_levels("ask", LEVEL)
        return prices, quantities


def bybit_orderbook_process(df):
    orderbook = BybitOrderBook()
    for i in range(len(df.loc[0, "data"]["a"])):
        df.loc[0, "data"]["a"][i] = [float(df.loc[0, "data"]["a"][i][0]), float(df.loc[0, "data"]["a"][i][1])]
        df.loc[0, "data"]["b"][i] = [float(df.loc[0, "data"]["b"][i][0]), float(df.loc[0, "data"]["b"][i][1])]
    orderbook.init_prices(df.loc[0, "data"]["a"], False)
    orderbook.init_prices(df.loc[0, "data"]["b"], True)
    ask_prices, ask_quantities = [], []
    bid_prices, bid_quantities = [], []
    timestamps = df["ts"].values[1 : len(df) - 1]
    for i in range(1, len(df) - 1):
        orderbook.update_string_prices(df.loc[i, "data"]["a"], False)
        orderbook.update_string_prices(df.loc[i, "data"]["b"], True)
        ask_price, ask_quantity = orderbook.get_asks()
        bid_price, bid_quantity = orderbook.get_bids()
        ask_prices.append(ask_price)
        ask_quantities.append(ask_quantity)
        bid_prices.append(bid_price)
        bid_quantities.append(bid_quantity)
    asks = np.transpose(np.array([ask_prices, ask_quantities]).T, (1, 0, 2))
    bids = np.transpose(np.array([bid_prices, bid_quantities]).T, (1, 0, 2))

    ob_summary = np.empty(asks.shape[0], dtype=orderbook_dt)
    ob_summary["timestamp"] = timestamps
    ob_summary["bid"] = bids
    ob_summary["ask"] = asks
    return ob_summary


def bybit_orderbook_transpose(orderbook_data):
    orderbook_data = np.array([orderbook_data["bid"], orderbook_data["ask"]], dtype=np.float64)
    orderbook_data = np.transpose(orderbook_data, (1, 0, 2, 3))
    return orderbook_data
