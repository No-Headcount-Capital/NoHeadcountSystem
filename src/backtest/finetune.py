import itertools
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# Ensure the src module is in the Python path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.dca_backtest import BacktestDCAStrategy, get_or_fetch_data
from src.backtest.runner import run_kline_backtest


def main():
    load_dotenv()

    print("正在加载数据...")
    opens, highs, lows, closes, signals, signal_flags, extras = get_or_fetch_data()
    print(f"数据加载完成，总数据量: {len(opens)}\n")

    # 固定参数 (继承自您的环境变量或安全预设)
    taker_fee = float(os.getenv("DCA_TAKER_FEE", 0.0004))
    stop_loss_rate = float(os.getenv("DCA_STOP_LOSS_RATE", 0.02))
    trade_volume = float(os.getenv("DCA_TRADE_VOLUME", 0.005))
    max_position = float(os.getenv("DCA_MAX_POSITION", 0.05))

    balance = 10000.0
    maker_commission = 0.0002
    price_tick = 0.1
    max_orders = 100

    # 待调优的参数网格 (Grid Search 空间)
    ema_alphas = [0.01, 0.02, 0.025, 0.03, 0.035, 0.04]
    target_profit_rates = [0.001, 0.002, 0.005, 0.0075, 0.01]
    dca_step_rates = [0.002, 0.003, 0.005, 0.0075, 0.01]

    # 生成所有可能的组合
    combinations = list(
        itertools.product(ema_alphas, target_profit_rates, dca_step_rates)
    )
    total_runs = len(combinations)
    print(f"开始网格搜索，共有 {total_runs} 种参数组合...\n")

    results_list = []

    for i, (ema_alpha, target_profit_rate, dca_step_rate) in enumerate(combinations):
        # 初始化策略
        strategy = BacktestDCAStrategy(
            ema_alpha=ema_alpha,
            taker_fee=taker_fee,
            target_profit_rate=target_profit_rate,
            dca_step_rate=dca_step_rate,
            stop_loss_rate=stop_loss_rate,
            trade_volume=trade_volume,
            max_position=max_position,
        )

        # 运行回测
        results = run_kline_backtest(
            strategy=strategy,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            signals=signals,
            signal_flags=signal_flags,
            extras=extras,
            maker_commission=maker_commission,
            taker_commission=taker_fee,
            balance=balance,
            price_tick=price_tick,
            max_orders=max_orders,
        )

        exchange = results[0]
        realized_pnl = exchange.realized_total_pnl
        turnover = exchange.turnover
        trades_count = len(exchange.realized_trade_pnls)

        print(
            f"[{i + 1}/{total_runs}] Alpha: {ema_alpha}, Profit: {target_profit_rate}, Step: {dca_step_rate} -> PNL: {realized_pnl:.2f}, Trades: {trades_count}"
        )

        # 记录本次组合结果
        results_list.append(
            {
                "ema_alpha": ema_alpha,
                "target_profit_rate": target_profit_rate,
                "dca_step_rate": dca_step_rate,
                "realized_pnl": realized_pnl,
                "turnover": turnover,
                "trades_count": trades_count,
            }
        )

    # 将结果转为 DataFrame 并按盈亏降序排序
    df_results = pd.DataFrame(results_list)
    df_sorted = df_results.sort_values(by="realized_pnl", ascending=False)

    print("\n" + "=" * 50)
    print("🏆 网格搜索完成！排名前 10 的最佳参数组合如下：")
    print("=" * 50)
    print(df_sorted.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
