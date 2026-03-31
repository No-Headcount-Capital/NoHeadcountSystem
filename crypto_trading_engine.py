from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from event.engine import EventEngine, Event  # 复用vnpy原生事件引擎

# ======================== 基础常量定义 ========================
# 事件类型定义
EVENT_CRYPTO_TICK = "eCryptoTick"       # 行情Tick事件
EVENT_CRYPTO_ORDER = "eCryptoOrder"     # 订单状态事件
EVENT_CRYPTO_TRADE = "eCryptoTrade"     # 成交回报事件
EVENT_CRYPTO_POSITION = "eCryptoPosition" # 持仓更新事件

# 交易方向/订单类型/开平类型枚举
class Direction(Enum):
    LONG = "多"    # 做多/买入
    SHORT = "空"   # 做空/卖出

class OrderType(Enum):
    LIMIT = "限价"
    MARKET = "市价"

class Offset(Enum):
    OPEN = "开仓"
    CLOSE = "平仓"

# ======================== 核心数据对象 ========================
class TickData:
    """加密货币Tick行情数据（模拟）"""
    def __init__(self):
        self.vt_symbol: str = ""  # 本地合约代码，如 BTC/USDT.BINANCE
        self.symbol: str = ""     # 基础标的，如 BTC/USDT
        self.exchange: str = ""   # 交易所，如 BINANCE/OKX
        self.datetime: datetime = datetime.now()
        self.last_price: float = 0.0  # 最新价
        self.bid_price_1: float = 0.0 # 买一价
        self.ask_price_1: float = 0.0 # 卖一价
        self.bid_volume_1: float = 0.0 # 买一量
        self.ask_volume_1: float = 0.0 # 卖一量

    def __str__(self):
        return (
            f"\nTick[{self.vt_symbol}] "
            f"最新价:{self.last_price:.2f} "
            f"买一:{self.bid_price_1:.2f}/{self.bid_volume_1} "
            f"卖一:{self.ask_price_1:.2f}/{self.ask_volume_1}"
        )

class OrderData:
    """加密货币订单数据（模拟）"""
    def __init__(self):
        self.vt_orderid: str = ""  # 本地订单ID
        self.vt_symbol: str = ""   # 合约代码
        self.direction: Direction = Direction.LONG
        self.offset: Offset = Offset.OPEN
        self.order_type: OrderType = OrderType.LIMIT
        self.price: float = 0.0
        self.volume: float = 0.0
        self.traded_volume: float = 0.0  # 已成交数量
        self.status: str = "SUBMITTING"  # 订单状态: SUBMITTING/TRADED/CANCELLED/REJECTED
        self.datetime: datetime = datetime.now()

    def __str__(self):
        return (
            f"\nOrder[{self.vt_orderid}] "
            f"{self.direction.value}{self.offset.value} "
            f"{self.vt_symbol} "
            f"价格:{self.price:.2f} 数量:{self.volume} 已成交:{self.traded_volume} "
            f"状态:{self.status}"
        )

class TradeData:
    """加密货币成交数据（模拟）"""
    def __init__(self):
        self.vt_tradeid: str = ""   # 本地成交ID
        self.vt_orderid: str = ""   # 关联订单ID
        self.vt_symbol: str = ""    # 合约代码
        self.direction: Direction = Direction.LONG
        self.offset: Offset = Offset.OPEN
        self.price: float = 0.0
        self.volume: float = 0.0
        self.datetime: datetime = datetime.now()

    def __str__(self):
        return (
            f"\nTrade[{self.vt_tradeid}] "
            f"{self.direction.value}{self.offset.value} "
            f"{self.vt_symbol} "
            f"价格:{self.price:.2f} 数量:{self.volume}"
        )

class PositionData:
    """加密货币持仓数据（模拟）"""
    def __init__(self):
        self.vt_symbol: str = ""    # 合约代码
        self.long_volume: float = 0.0  # 多头持仓
        self.short_volume: float = 0.0 # 空头持仓
        self.long_avg_price: float = 0.0 # 多头均价
        self.short_avg_price: float = 0.0 # 空头均价

    def __str__(self):
        return (
            f"Position[{self.vt_symbol}] "
            f"多头:{self.long_volume}({self.long_avg_price}) "
            f"空头:{self.short_volume}({self.short_avg_price})"
        )

# ======================== 模拟加密货币网关 ========================
class MockCryptoGateway:
    """模拟加密货币交易所网关（核心）"""
    def __init__(self, event_engine: EventEngine):
        self.event_engine: EventEngine = event_engine
        self.exchange_name: str = "MOCK_CRYPTO"  # 模拟交易所名称
        
        # 模拟存储：订单/成交/持仓/行情
        self.orders: Dict[str, OrderData] = {}  # vt_orderid -> OrderData
        self.trades: Dict[str, TradeData] = {}  # vt_tradeid -> TradeData
        self.positions: Dict[str, PositionData] = defaultdict(PositionData)  # vt_symbol -> PositionData
        self.ticks: Dict[str, TickData] = {}    # vt_symbol -> TickData

        # 订单ID/成交ID自增器
        self.order_id_counter: int = 10000
        self.trade_id_counter: int = 20000

        # 启动模拟行情推送（每秒更新）
        self._simulate_tick_running: bool = False
        self._simulate_tick_thread = None

    def start(self):
        """启动网关（模拟行情推送）"""
        self._simulate_tick_running = True
        import threading
        self._simulate_tick_thread = threading.Thread(target=self._simulate_tick_loop)
        self._simulate_tick_thread.start()
        print(f"[{self.exchange_name}] 模拟加密货币网关已启动")

    def stop(self):
        """停止网关"""
        self._simulate_tick_running = False
        if self._simulate_tick_thread:
            self._simulate_tick_thread.join()
        print(f"[{self.exchange_name}] 模拟加密货币网关已停止")

    def _simulate_tick_loop(self):
        """模拟行情推送循环（每秒生成随机波动的Tick）"""
        import time
        import random

        # 初始化基础行情
        base_symbols = ["BTC/USDT.MOCK", "ETH/USDT.MOCK"]
        for vt_symbol in base_symbols:
            tick = TickData()
            tick.vt_symbol = vt_symbol
            tick.symbol = vt_symbol.split(".")[0]
            tick.exchange = vt_symbol.split(".")[1]
            # 初始价格：BTC=50000, ETH=3000
            tick.last_price = 50000.0 if "BTC" in vt_symbol else 3000.0
            tick.bid_price_1 = tick.last_price * 0.999
            tick.ask_price_1 = tick.last_price * 1.001
            tick.bid_volume_1 = random.uniform(1, 5)
            tick.ask_volume_1 = random.uniform(1, 5)
            self.ticks[vt_symbol] = tick

        # 循环更新行情
        while self._simulate_tick_running:
            time.sleep(1)
            for vt_symbol, tick in self.ticks.items():
                # 随机波动±0.1%
                volatility = random.uniform(-0.001, 0.001)
                tick.last_price *= (1 + volatility)
                tick.bid_price_1 = tick.last_price * 0.999
                tick.ask_price_1 = tick.last_price * 1.001
                tick.bid_volume_1 = random.uniform(1, 5)
                tick.ask_volume_1 = random.uniform(1, 5)
                tick.datetime = datetime.now()

                # 推送Tick事件到事件引擎
                event = Event(EVENT_CRYPTO_TICK, tick)
                self.event_engine.put(event)

    def send_order(self, vt_symbol: str, direction: Direction, offset: Offset, 
                   price: float, volume: float, order_type: OrderType = OrderType.LIMIT) -> str:
        """
        发送委托订单（模拟撮合）
        :return: 本地订单ID
        """
        # 1. 生成订单对象
        order = OrderData()
        order.vt_orderid = f"{self.exchange_name}.{self.order_id_counter}"
        self.order_id_counter += 1
        order.vt_symbol = vt_symbol
        order.direction = direction
        order.offset = offset
        order.order_type = order_type
        order.price = price
        order.volume = volume
        order.status = "SUBMITTED"
        self.orders[order.vt_orderid] = order

        # 2. 推送订单状态事件
        event = Event(EVENT_CRYPTO_ORDER, order)
        self.event_engine.put(event)

        # 3. 模拟交易所撮合
        self._simulate_order_matching(order)

        print(f"[{self.exchange_name}] 发送订单: {order}")
        return order.vt_orderid

    def _simulate_order_matching(self, order: OrderData):
        """模拟订单撮合逻辑"""
        tick = self.ticks.get(order.vt_symbol)
        if not tick:
            order.status = "REJECTED"
            event = Event(EVENT_CRYPTO_ORDER, order)
            self.event_engine.put(event)
            print(f"[{self.exchange_name}] 订单被拒：无行情 {order.vt_symbol}")
            return

        # 市价单：直接按对手价成交
        if order.order_type == OrderType.MARKET:
            match_price = tick.ask_price_1 if order.direction == Direction.LONG else tick.bid_price_1
            self._match_order(order, match_price, order.volume)
        # 限价单：价格满足则成交
        elif order.order_type == OrderType.LIMIT:
            if (order.direction == Direction.LONG and order.price >= tick.ask_price_1) or \
               (order.direction == Direction.SHORT and order.price <= tick.bid_price_1):
                self._match_order(order, order.price, order.volume)

    def _match_order(self, order: OrderData, match_price: float, match_volume: float):
        """完成订单撮合，更新成交/持仓"""
        # 1. 更新订单成交状态
        order.traded_volume = match_volume
        order.status = "TRADED"
        order_event = Event(EVENT_CRYPTO_ORDER, order)
        self.event_engine.put(order_event)

        # 2. 生成成交数据
        trade = TradeData()
        trade.vt_tradeid = f"{self.exchange_name}.{self.trade_id_counter}"
        self.trade_id_counter += 1
        trade.vt_orderid = order.vt_orderid
        trade.vt_symbol = order.vt_symbol
        trade.direction = order.direction
        trade.offset = order.offset
        trade.price = match_price
        trade.volume = match_volume
        trade.datetime = datetime.now()
        self.trades[trade.vt_tradeid] = trade

        # 3. 推送成交事件
        trade_event = Event(EVENT_CRYPTO_TRADE, trade)
        self.event_engine.put(trade_event)

        # 4. 更新持仓
        position = self.positions[order.vt_symbol]
        if order.direction == Direction.LONG:
            if order.offset == Offset.OPEN:
                # 多头开仓：加权平均计算均价
                total_volume = position.long_volume + match_volume
                position.long_avg_price = (
                    position.long_avg_price * position.long_volume + match_price * match_volume
                ) / total_volume if total_volume > 0 else 0.0
                position.long_volume = total_volume
            else:
                # 多头平仓：减少空头持仓（加密货币多空独立）
                position.short_volume = max(0, position.short_volume - match_volume)
        else:
            if order.offset == Offset.OPEN:
                # 空头开仓：加权平均计算均价
                total_volume = position.short_volume + match_volume
                position.short_avg_price = (
                    position.short_avg_price * position.short_volume + match_price * match_volume
                ) / total_volume if total_volume > 0 else 0.0
                position.short_volume = total_volume
            else:
                # 空头平仓：减少多头持仓
                position.long_volume = max(0, position.long_volume - match_volume)

        # 5. 推送持仓事件
        position_event = Event(EVENT_CRYPTO_POSITION, position)
        self.event_engine.put(position_event)

        print(f"[{self.exchange_name}] 订单成交: {trade}")

    def cancel_order(self, vt_orderid: str):
        """撤销订单（模拟）"""
        order = self.orders.get(vt_orderid)
        if not order:
            print(f"[{self.exchange_name}] 撤销失败：订单不存在 {vt_orderid}")
            return
        if order.status in ["TRADED", "CANCELLED", "REJECTED"]:
            print(f"[{self.exchange_name}] 撤销失败：订单状态不允许 {order}")
            return

        order.status = "CANCELLED"
        event = Event(EVENT_CRYPTO_ORDER, order)
        self.event_engine.put(event)
        print(f"[{self.exchange_name}] 订单已撤销: {order}")

    def get_position(self, vt_symbol: str) -> PositionData:
        """查询持仓"""
        return self.positions.get(vt_symbol, PositionData())

# ======================== 加密货币策略模板 ========================
class CryptoStrategyTemplate:
    """加密货币策略模板（适配事件引擎）"""
    def __init__(self, strategy_name: str, event_engine: EventEngine, gateway: MockCryptoGateway):
        self.strategy_name: str = strategy_name
        self.event_engine: EventEngine = event_engine
        self.gateway: MockCryptoGateway = gateway
        
        # 策略状态
        self.inited: bool = False
        self.trading: bool = False

        # 注册事件处理函数
        self._register_event_handlers()

    def _register_event_handlers(self):
        """注册事件处理器"""
        self.event_engine.register(EVENT_CRYPTO_TICK, self.on_tick)
        self.event_engine.register(EVENT_CRYPTO_ORDER, self.on_order)
        self.event_engine.register(EVENT_CRYPTO_TRADE, self.on_trade)
        self.event_engine.register(EVENT_CRYPTO_POSITION, self.on_position)

    def on_tick(self, event: Event):
        """行情Tick事件处理（需子类重写）"""
        tick: TickData = event.data
        # 子类实现具体策略逻辑
        pass

    def on_order(self, event: Event):
        """订单状态事件处理"""
        order: OrderData = event.data
        self.write_log(f"订单状态更新: {order}")

    def on_trade(self, event: Event):
        """成交事件处理"""
        trade: TradeData = event.data
        self.write_log(f"成交回报: {trade}")

    def on_position(self, event: Event):
        """持仓更新事件处理"""
        position: PositionData = event.data
        self.write_log(f"持仓更新: {position}")

    def write_log(self, msg: str):
        """打印策略日志"""
        print(f"[{self.strategy_name}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}")

    def buy(self, vt_symbol: str, price: float, volume: float, order_type: OrderType = OrderType.LIMIT) -> str:
        """买入开仓"""
        if not self.trading:
            self.write_log("买入失败：策略未启动")
            return ""
        return self.gateway.send_order(vt_symbol, Direction.LONG, Offset.OPEN, price, volume, order_type)

    def sell(self, vt_symbol: str, price: float, volume: float, order_type: OrderType = OrderType.LIMIT) -> str:
        """卖出平仓"""
        if not self.trading:
            self.write_log("卖出失败：策略未启动")
            return ""
        return self.gateway.send_order(vt_symbol, Direction.SHORT, Offset.CLOSE, price, volume, order_type)

    def short(self, vt_symbol: str, price: float, volume: float, order_type: OrderType = OrderType.LIMIT) -> str:
        """卖出开仓（做空）"""
        if not self.trading:
            self.write_log("做空失败：策略未启动")
            return ""
        return self.gateway.send_order(vt_symbol, Direction.SHORT, Offset.OPEN, price, volume, order_type)

    def cover(self, vt_symbol: str, price: float, volume: float, order_type: OrderType = OrderType.LIMIT) -> str:
        """买入平仓（平空）"""
        if not self.trading:
            self.write_log("平空失败：策略未启动")
            return ""
        return self.gateway.send_order(vt_symbol, Direction.LONG, Offset.CLOSE, price, volume, order_type)

    def cancel_order(self, vt_orderid: str):
        """撤销订单"""
        self.gateway.cancel_order(vt_orderid)

    def start(self):
        """启动策略"""
        self.trading = True
        self.inited = True
        self.write_log("策略已启动")

    def stop(self):
        """停止策略"""
        self.trading = False
        self.write_log("策略已停止")

# ======================== 示例策略：加密货币网格策略 ========================
class CryptoGridStrategy(CryptoStrategyTemplate):
    """加密货币网格策略（示例）"""
    def __init__(self, strategy_name: str, event_engine: EventEngine, gateway: MockCryptoGateway):
        super().__init__(strategy_name, event_engine, gateway)
        
        # 网格策略参数
        self.vt_symbol: str = "BTC/USDT.MOCK"  # 交易标的
        self.grid_step: float = 100.0          # 网格步长
        self.grid_volume: float = 0.01         # 每格交易量
        self.upper_price: float = 51000.0      # 网格上沿
        self.lower_price: float = 49000.0      # 网格下沿

        # 策略状态
        self.last_grid_price: float = 0.0      # 上一次成交的网格价格

    def on_tick(self, event: Event):
        """Tick行情驱动网格交易"""
        if not self.trading:
            return
        
        tick: TickData = event.data
        if tick.vt_symbol != self.vt_symbol:
            return

        current_price = tick.last_price
        # 价格高于上沿：平仓所有多头
        if current_price >= self.upper_price and self.last_grid_price != self.upper_price:
            position = self.gateway.get_position(self.vt_symbol)
            if position.long_volume > 0:
                self.sell(self.vt_symbol, tick.ask_price_1, position.long_volume, OrderType.MARKET)
                self.last_grid_price = self.upper_price
                self.write_log(f"触发上沿平仓：{current_price} >= {self.upper_price}")
        # 价格低于下沿：开仓多头
        elif current_price <= self.lower_price and self.last_grid_price != self.lower_price:
            self.buy(self.vt_symbol, tick.bid_price_1, self.grid_volume, OrderType.MARKET)
            self.last_grid_price = self.lower_price
            self.write_log(f"触发下沿开仓：{current_price} <= {self.lower_price}")
        # 价格在网格区间内：按步长做高抛低吸
        elif self.lower_price < current_price < self.upper_price:
            current_grid = round(current_price / self.grid_step) * self.grid_step
            if current_grid > self.last_grid_price + self.grid_step/2:
                # 价格上涨超过半格：卖出（平仓）
                self.sell(self.vt_symbol, tick.ask_price_1, self.grid_volume, OrderType.MARKET)
                self.last_grid_price = current_grid
                self.write_log(f"网格止盈：{current_grid}")
            elif current_grid < self.last_grid_price - self.grid_step/2:
                # 价格下跌超过半格：买入（开仓）
                self.buy(self.vt_symbol, tick.bid_price_1, self.grid_volume, OrderType.MARKET)
                self.last_grid_price = current_grid
                self.write_log(f"网格建仓：{current_grid}")

# ======================== 主程序：整合运行 ========================
def main():
    # 1. 初始化事件引擎
    event_engine = EventEngine()
    event_engine.start()
    print("事件引擎已启动")

    # 2. 初始化模拟加密货币网关
    gateway = MockCryptoGateway(event_engine)
    gateway.start()

    # 3. 初始化并启动网格策略
    grid_strategy = CryptoGridStrategy("BTC_GRID_STRATEGY", event_engine, gateway)
    grid_strategy.start()

    # 4. 保持程序运行
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        # 5. 优雅退出
        print("\n开始停止系统...")
        grid_strategy.stop()
        gateway.stop()
        event_engine.stop()
        print("系统已停止")

if __name__ == "__main__":
    main()