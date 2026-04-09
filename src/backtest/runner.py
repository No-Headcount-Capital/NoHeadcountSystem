from .loop import bind_and_compile_kline_strategy, kline_backtest


def run_kline_backtest(
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
    bind_and_compile_kline_strategy(strategy)
    return kline_backtest(
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
