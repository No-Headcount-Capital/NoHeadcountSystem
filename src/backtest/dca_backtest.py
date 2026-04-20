import os
import sys
from pathlib import Path

import ccxt
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Ensure the src module is in the Python path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from numba import float64
from numba.experimental import jitclass

from src.backtest.runner import run_kline_backtest

# 必须为 Numba 提前声明所有类属性的内存类型
spec = [
    ("ema_alpha", float64),
    ("taker_fee", float64),
    ("target_profit_rate", float64),
    ("dca_step_rate", float64),
    ("stop_loss_rate", float64),
    ("trade_volume", float64),
    ("max_position", float64),
    ("min_roi", float64),
    ("entry_threshold", float64),
    ("ema_mid_price", float64),
]


@jitclass(spec)
class BacktestDCAStrategy:
    """
    回测专用的 Numba 兼容版 DCA 手续费感知型策略。
    使用 K 线的收盘价替代 Tick 的买卖一价进行逻辑模拟。
    """

    def __init__(
        self,
        ema_alpha: float = 0.005,
        taker_fee: float = 0.0004,
        target_profit_rate: float = 0.0002,
        dca_step_rate: float = 0.0015,
        stop_loss_rate: float = 0.02,
        trade_volume: float = 1.0,
        max_position: float = 5.0,
    ):
        self.ema_alpha = ema_alpha
        self.taker_fee = taker_fee
        self.target_profit_rate = target_profit_rate
        self.dca_step_rate = dca_step_rate
        self.stop_loss_rate = stop_loss_rate
        self.trade_volume = trade_volume
        self.max_position = max_position

        # 核心：计算必须跨越的硬性利润率门槛
        self.min_roi = (taker_fee * 2) + target_profit_rate
        self.entry_threshold = self.min_roi * 1.2

        # 0.0 代表尚未初始化
        self.ema_mid_price = 0.0

    # -------------------------------------------------------------
    # 回测框架必须实现的 3 个接口
    # -------------------------------------------------------------

    def on_kline(self, open_signal, exchange):
        # 依赖于外部信号数组的开盘逻辑，本策略主要基于纯价格计算，这里留空
        pass

    def on_order(self, filled_order, exchange):
        # 订单成交时的回调，限价单会用到。市价单直接成交，无需特殊处理
        pass

    def on_tick(self, kline, exchange):
        # 在回测的内部逻辑中，on_tick 发生在每根 K 线的末尾，此时的 reference_price 是收盘价
        mid_price = kline.close

        # 初始化 EMA
        if self.ema_mid_price == 0.0:
            self.ema_mid_price = mid_price
            return

        # 更新均线
        self.ema_mid_price = (
            self.ema_alpha * mid_price + (1.0 - self.ema_alpha) * self.ema_mid_price
        )

        # 计算价格偏离度
        dev_rate = (mid_price - self.ema_mid_price) / self.ema_mid_price

        # 从回测引擎中直接获取精准的持仓和均价，无需自己计算！
        pos = exchange.position
        avg_price = exchange.cost_price

        # ---------------- 1. 止盈与止损逻辑 (Exit Logic) ----------------
        if pos > 0:
            roi = (mid_price - avg_price) / avg_price
            if roi >= self.min_roi or roi <= -self.stop_loss_rate:
                exchange.place_market_order(-pos)  # 发送相反数量的市价单全平
                return  # 平仓后本回合不再开仓

        elif pos < 0:
            roi = (avg_price - mid_price) / avg_price
            if roi >= self.min_roi or roi <= -self.stop_loss_rate:
                exchange.place_market_order(-pos)  # 发送相反数量的市价单全平
                return  # 平仓后本回合不再开仓

        # ---------------- 2. 建仓与网格加仓逻辑 (Entry & DCA) ----------------
        if pos == 0:
            # 空仓状态，寻找极端偏离均线的机会
            if dev_rate < -self.entry_threshold:
                exchange.place_market_order(self.trade_volume)
            elif dev_rate > self.entry_threshold:
                exchange.place_market_order(-self.trade_volume)

        elif pos > 0 and pos < self.max_position:
            # 多头网格加仓：价格比均价还要便宜 dca_step_rate
            if mid_price < avg_price * (1.0 - self.dca_step_rate):
                exchange.place_market_order(self.trade_volume)

        elif pos < 0 and pos > -self.max_position:
            # 空头网格加仓：价格比均价还要贵 dca_step_rate
            if mid_price > avg_price * (1.0 + self.dca_step_rate):
                exchange.place_market_order(-self.trade_volume)


def get_or_fetch_data(file_path: str | Path = "data/test.parquet"):
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    # file_path = data_dir / "BTCUSDT_1m_20170817_20260420.parquet"
    file_path = Path(file_path)
    if file_path.exists():
        print("读取本地缓存数据...")
        df = pd.read_parquet(file_path)
    else:
        print("使用 ccxt 下载最近一月的 BTC/USDT 1m K线数据...")
        exchange = ccxt.okx()
        since = exchange.milliseconds() - 60 * 24 * 60 * 60 * 1000
        all_ohlcv = []
        while since < exchange.milliseconds():
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1m", since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 60 * 1000
        df = pd.DataFrame(
            all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df.to_csv(file_path, index=False)

    opens = df["open"].values.astype(np.float64)
    highs = df["high"].values.astype(np.float64)
    lows = df["low"].values.astype(np.float64)
    closes = df["close"].values.astype(np.float64)
    n = len(df)
    signals = np.zeros(n, dtype=np.float64)
    signal_flags = np.ones(n, dtype=np.int32)
    extras = np.zeros((n, 1), dtype=np.float64)

    return opens, highs, lows, closes, signals, signal_flags, extras


if __name__ == "__main__":
    load_dotenv()

    # 从环境变量获取策略参数，如果没有则使用默认值
    ema_alpha = float(os.getenv("DCA_EMA_ALPHA", 0.005))
    taker_fee = float(os.getenv("DCA_TAKER_FEE", 0.0004))
    target_profit_rate = float(os.getenv("DCA_TARGET_PROFIT_RATE", 0.0002))
    dca_step_rate = float(os.getenv("DCA_STEP_RATE", 0.0015))
    stop_loss_rate = float(os.getenv("DCA_STOP_LOSS_RATE", 0.02))
    trade_volume = float(os.getenv("DCA_TRADE_VOLUME", 1.0))
    max_position = float(os.getenv("DCA_MAX_POSITION", 5.0))

    opens, highs, lows, closes, signals, signal_flags, extras = get_or_fetch_data()

    strategy = BacktestDCAStrategy(
        ema_alpha=ema_alpha,
        taker_fee=taker_fee,
        target_profit_rate=target_profit_rate,
        dca_step_rate=dca_step_rate,
        stop_loss_rate=stop_loss_rate,
        trade_volume=trade_volume,
        max_position=max_position,
    )

    print(f"开始回测 DCA 策略，数据量: {len(opens)}")
    results = run_kline_backtest(
        strategy=strategy,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        signals=signals,
        signal_flags=signal_flags,
        extras=extras,  # 额外数据
        maker_commission=0.0002,  # Maker手续费 (限价单) 0.02%
        taker_commission=taker_fee,  # 动态读取的Taker手续费
        balance=10000.0,  # 初始资金
        price_tick=0.1,  # 最小价格变动单位（跳动点）
        max_orders=100,  # 最多同时挂单数量限制
    )

    print("回测结束。结果：")

    exchange = results[0]
    pnls = results[1]

    initial_balance = 10000.0
    equity_curve = initial_balance + pnls

    # 指标计算
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

    print(f"初始资金: {exchange.balance}")
    print(f"实现盈亏: {exchange.realized_total_pnl:.2f}")
    print(f"交易额: {exchange.turnover:.2f}")
    print(f"结束持仓: {exchange.position}")
    print(f"平仓笔数: {len(exchange.realized_trade_pnls)}")
    print("-" * 30)
    print(f"年化收益率: {annual_return * 100:.2f}%")
    print(f"年化波动率: {annual_volatility * 100:.2f}%")
    print(f"夏普比率: {sharpe_ratio:.2f}")
    print(f"最大回撤: {max_drawdown * 100:.2f}%")

    # 绘制净值曲线
    if len(equity_curve) > 0:
        plt.figure(figsize=(12, 6))
        plt.plot(equity_curve, label="Equity Curve")
        plt.title("Backtest Equity Curve")
        plt.xlabel("Time (1m bars)")
        plt.ylabel("Equity (USDT)")
        plt.grid(True)
        plt.legend()

        output_file = "./data/equity_curve.png"
        plt.savefig(output_file)
        print(f"\n净值曲线已保存至: {output_file}")
