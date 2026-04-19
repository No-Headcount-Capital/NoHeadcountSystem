from typing import Optional
from src.infrastructure.models import Direction, Offset, OrderData, TickData, TradeData
from src.strategies.base import BaseStrategy


class FeeAwareStatArbStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_name: str,
        event_engine,
        vt_symbol: str,
        ema_alpha: float = 0.005,           # EMA基准价的平滑因子
        taker_fee: float = 0.0004,          # Taker手续费(万分之四)
        target_profit_rate: float = 0.0002, # 目标净利(万分之二)
        dca_step_rate: float = 0.0015,      # 网格加仓的价差步长(千分之1.5)
        stop_loss_rate: float = 0.02,       # 单边灾难止损(2%)
        trade_volume: float = 1.0,          # 单笔交易数量
        max_position: float = 5.0,          # 最大持仓量
    ) -> None:
        super().__init__(strategy_name=strategy_name, event_engine=event_engine, vt_symbol=vt_symbol)

        self.ema_alpha = ema_alpha
        self.taker_fee = taker_fee
        self.target_profit_rate = target_profit_rate
        self.dca_step_rate = dca_step_rate
        self.stop_loss_rate = stop_loss_rate
        self.trade_volume = trade_volume
        self.max_position = max_position

        # 必须跨越的硬性门槛：双边 Taker 手续费 + 目标净利
        self.min_roi = (self.taker_fee * 2) + self.target_profit_rate
        # 入场阈值：偏离均线超过预期盈利幅度
        self.entry_threshold = self.min_roi * 1.2

        self.ema_mid_price: Optional[float] = None

        # 仓位与均价管理
        self.current_position: float = 0.0
        self.pending_position: float = 0.0
        self.avg_entry_price: float = 0.0

    def on_tick(self, tick: TickData) -> None:
        mid_price = (tick.ask_price_1 + tick.bid_price_1) / 2.0

        # 初始化基准价
        if self.ema_mid_price is None:
            self.ema_mid_price = mid_price
            return

        # 更新 EMA
        self.ema_mid_price = self.ema_alpha * mid_price + (1 - self.ema_alpha) * self.ema_mid_price

        # 计算当前微观价格偏离度
        dev_rate = (mid_price - self.ema_mid_price) / self.ema_mid_price
        target_position = self.pending_position

        # ---------------- 1. 止盈止损逻辑 ----------------
        if self.pending_position > 0 and self.avg_entry_price > 0:
            # 假设做多，市价卖平对应 bid_price_1
            roi = (tick.bid_price_1 - self.avg_entry_price) / self.avg_entry_price
            if roi >= self.min_roi or roi <= -self.stop_loss_rate:
                target_position = 0.0

        elif self.pending_position < 0 and self.avg_entry_price > 0:
            # 假设做空，市价买平对应 ask_price_1
            roi = (self.avg_entry_price - tick.ask_price_1) / self.avg_entry_price
            if roi >= self.min_roi or roi <= -self.stop_loss_rate:
                target_position = 0.0

        # ---------------- 2. 开仓与网格加仓 ----------------
        if target_position == 0:
            # 空仓寻找偏离入场
            if dev_rate < -self.entry_threshold:
                target_position = self.trade_volume
            elif dev_rate > self.entry_threshold:
                target_position = -self.trade_volume

        elif target_position > 0 and target_position < self.max_position:
            # 多头被套，且最新要买入的价格足够便宜则网格加仓摊平成本
            if tick.ask_price_1 < self.avg_entry_price * (1 - self.dca_step_rate):
                target_position += self.trade_volume

        elif target_position < 0 and target_position > -self.max_position:
            # 空头被套，且最新要卖出的价格足够高则网格加仓摊平成本
            if tick.bid_price_1 > self.avg_entry_price * (1 + self.dca_step_rate):
                target_position -= self.trade_volume

        # ---------------- 3. 执行调仓指令 ----------------
        if target_position != self.pending_position:
            self._adjust_position(target_position, tick)

    def _adjust_position(self, target: float, tick: TickData) -> None:
        """ 智能仓单分拆：将平仓(CLOSE)和开仓(OPEN)合法发送给系统 """
        diff = target - self.pending_position

        if diff > 0:
            # 需要买入
            if self.pending_position < 0:
                vol_to_close = min(abs(self.pending_position), diff)
                self.emit_signal(
                    direction=Direction.LONG,
                    price=tick.ask_price_1,
                    volume=vol_to_close,
                    offset=Offset.CLOSE
                )
                diff -= vol_to_close

            if diff > 0:
                self.emit_signal(
                    direction=Direction.LONG,
                    price=tick.ask_price_1,
                    volume=diff,
                    offset=Offset.OPEN
                )

        elif diff < 0:
            # 需要卖出
            abs_diff = abs(diff)
            if self.pending_position > 0:
                vol_to_close = min(self.pending_position, abs_diff)
                self.emit_signal(
                    direction=Direction.SHORT,
                    price=tick.bid_price_1,
                    volume=vol_to_close,
                    offset=Offset.CLOSE
                )
                abs_diff -= vol_to_close

            if abs_diff > 0:
                self.emit_signal(
                    direction=Direction.SHORT,
                    price=tick.bid_price_1,
                    volume=abs_diff,
                    offset=Offset.OPEN
                )

        self.pending_position = target

    def on_order(self, order: OrderData) -> None:
        print(f"[STAT_ARB_ORDER] id={order.order_id} status={order.status.value} price={order.price} volume={order.volume}")

    def on_trade(self, trade: TradeData) -> None:
        print(f"[STAT_ARB_TRADE] trade_id={trade.trade_id} price={trade.price} volume={trade.volume}")

        # 精确维护实际仓位均价以支持 roi 计算
        # 注意：这里默认您的 TradeData 实现了 direction 和 offset 属性
        try:
            is_opening = (trade.offset == Offset.OPEN)

            if is_opening:
                # 计算持仓量变化
                trade_dir_vol = trade.volume if trade.direction == Direction.LONG else -trade.volume
                new_pos = self.current_position + trade_dir_vol

                # 计算加权平均价
                if self.current_position == 0:
                    self.avg_entry_price = trade.price
                else:
                    prev_value = abs(self.current_position) * self.avg_entry_price
                    trade_value = trade.volume * trade.price
                    self.avg_entry_price = (prev_value + trade_value) / abs(new_pos)

                self.current_position = new_pos
            else:
                # 平仓时持仓量变化 (LONG平空，SHORT平多)
                trade_dir_vol = trade.volume if trade.direction == Direction.LONG else -trade.volume
                self.current_position += trade_dir_vol

                # 如果持仓归零，重置均价
                if round(self.current_position, 6) == 0:
                    self.current_position = 0.0
                    self.avg_entry_price = 0.0

        except AttributeError:
            pass  # 防止底层 TradeData 缺乏 direction/offset 时代码崩溃
