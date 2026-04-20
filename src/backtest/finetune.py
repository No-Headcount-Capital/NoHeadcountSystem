import itertools
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
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
    opens, highs, lows, closes, signals, signal_flags, extras = get_or_fetch_data(
        "data/train.parquet"
    )
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
    ema_alphas = [0.025, 0.03, 0.035, 0.04, 0.05, 0.075, 0.1]
    target_profit_rates = [0.01, 0.015, 0.02, 0.025, 0.03]
    dca_step_rates = [0.005, 0.0075, 0.01, 0.015, 0.02]

    # 生成所有可能的组合
    combinations = list(
        itertools.product(ema_alphas, target_profit_rates, dca_step_rates)
    )
    total_runs = len(combinations)
    print(f"开始网格搜索，共有 {total_runs} 种参数组合...\n")

    results_list = []
    best_sharpe = -float("inf")
    best_equity_curve = None
    best_params = None

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
        pnls = results[1]

        initial_balance = balance
        equity_curve = initial_balance + pnls

        if len(equity_curve) > 0:
            total_years = len(equity_curve) / (365.25 * 24 * 60)
            total_return = (equity_curve[-1] - initial_balance) / initial_balance
            annual_return = (
                (1 + total_return) ** (1 / total_years) - 1 if total_years > 0 else 0.0
            )

            returns = np.diff(equity_curve) / equity_curve[:-1]
            annual_volatility = np.std(returns) * np.sqrt(365.25 * 24 * 60)
            sharpe_ratio = (
                annual_return / annual_volatility if annual_volatility > 0 else 0.0
            )

            cummax = np.maximum.accumulate(equity_curve)
            drawdowns = (cummax - equity_curve) / cummax
            max_drawdown = np.max(drawdowns)
        else:
            annual_return = 0.0
            annual_volatility = 0.0
            sharpe_ratio = 0.0
            max_drawdown = 0.0

        if sharpe_ratio > best_sharpe:
            best_sharpe = sharpe_ratio
            best_equity_curve = equity_curve.copy() if len(equity_curve) > 0 else np.array([])
            best_params = (ema_alpha, target_profit_rate, dca_step_rate)

        realized_pnl = exchange.realized_total_pnl
        turnover = exchange.turnover
        trades_count = len(exchange.realized_trade_pnls)

        print(
            f"[{i + 1}/{total_runs}] Alpha: {ema_alpha}, Profit: {target_profit_rate}, Step: {dca_step_rate} -> PNL: {realized_pnl:.2f}, Sharpe: {sharpe_ratio:.2f}, Trades: {trades_count}"
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
                "annual_return": annual_return,
                "annual_volatility": annual_volatility,
                "sharpe_ratio": sharpe_ratio,
                "max_drawdown": max_drawdown,
            }
        )

    # 将结果转为 DataFrame 并按夏普比率降序排序
    df_results = pd.DataFrame(results_list)
    df_sorted = df_results.sort_values(by="sharpe_ratio", ascending=False)

    print("\n" + "=" * 50)
    print("🏆 网格搜索完成！排名前 10 的最佳参数组合如下：")
    print("=" * 50)
    print(df_sorted.head(10).to_string(index=False))


    if best_equity_curve is not None and len(best_equity_curve) > 0:
        plt.figure(figsize=(12, 6))
        plt.plot(best_equity_curve, label=f"Sharpe Ratio: {best_sharpe:.2f}")
        plt.title(
            f"Best Equity Curve\nAlpha: {best_params[0]} | Target Profit: {best_params[1]} | Step Rate: {best_params[2]}"
        )
        plt.xlabel("Time (1m bars)")
        plt.ylabel("Equity (USDT)")
        plt.grid(True)
        plt.legend()

        output_file = "finetune_best_equity_curve.png"
        plt.savefig(output_file)
        print(f"\n最佳参数组合的净值曲线已保存至: {output_file}\n")

if __name__ == "__main__":
    main()
