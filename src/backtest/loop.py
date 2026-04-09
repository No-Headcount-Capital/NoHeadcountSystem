import numpy as np
from numba import float64, int64, njit

from .events import Kline, OpenSignal
from .exchange import KlineExchange
from .strategy import strategy_type

_compiled = None


def bind_and_compile_kline_strategy(defined_strategy_type):
    global _compiled
    if _compiled is not None:
        return _compiled

    if hasattr(defined_strategy_type, "class_type"):
        instance_type = defined_strategy_type.class_type.instance_type
    elif hasattr(defined_strategy_type, "_numba_type_"):
        instance_type = defined_strategy_type._numba_type_
    else:
        raise TypeError("Expected a jitclass type or instance.")

    strategy_type.define(instance_type)

    @njit
    def _backtest_cmpl(
        strategy,
        opens,
        highs,
        lows,
        closes,
        signals,
        signal_flags,
        extras,
        maker_commission,
        taker_commission,
        balance,
        price_tick,
        max_orders,
    ):
        exchange = KlineExchange(maker_commission, taker_commission, balance, price_tick, max_orders)
        pnls = np.zeros(int64(np.sum(signal_flags)), dtype=np.float64)
        count = 0
        for i in range(len(opens)):
            if signal_flags[i] == 1 and i > 0:
                exchange.set_reference_price(opens[i])
                open_signal = OpenSignal(opens[i], signals[i], extras[i])
                strategy.on_kline(open_signal, exchange)

                pnls[count] = exchange.realized_total_pnl + exchange.get_unrealized_pnl(opens[i])
                count += 1

            filled_order = exchange.match_limit_order(highs[i])
            if filled_order is not None:
                strategy.on_order(filled_order, exchange)

            filled_order = exchange.match_limit_order(lows[i])
            if filled_order is not None:
                strategy.on_order(filled_order, exchange)

            kline = Kline(opens[i], highs[i], lows[i], closes[i], signals[i], signal_flags[i], extras[i])
            exchange.set_reference_price(closes[i])
            strategy.on_tick(kline, exchange)
        return exchange, pnls[:-1]

    _compiled = _backtest_cmpl
    return _backtest_cmpl


def kline_backtest(
    strategy,
    opens,
    highs,
    lows,
    closes,
    signals,
    signal_flags,
    extras,
    maker_commission,
    taker_commission,
    balance,
    price_tick,
    max_orders,
):
    if _compiled is None:
        raise RuntimeError("Call bind_and_compile_kline_strategy() first.")
    return _compiled(
        strategy,
        opens,
        highs,
        lows,
        closes,
        signals,
        signal_flags,
        extras,
        maker_commission,
        taker_commission,
        balance,
        price_tick,
        max_orders,
    )
