import json
import hmac
import hashlib
import time
import threading
import queue
import requests
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Any, Optional
from dotenv import load_dotenv
import os
from urllib.parse import urlencode  # 在文件顶部的 import 区添加

# ====================== 全局信号处理（解决Ctrl+C无法终止） ======================
def signal_handler(signum, frame):
    """处理Ctrl+C中断信号"""
    print("\n🛑 接收到中断信号，开始安全退出...")
    # 停止全局事件引擎（如果存在）
    if 'event_engine' in globals() and event_engine:
        event_engine.stop()
    # 停止行情轮询线程
    if 'gateway' in globals() and gateway:
        gateway.stop_ticker_polling()
    sys.exit(0)

# 注册信号处理
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ====================== 1. 常量定义（对标VNPY） ======================
class Exchange(Enum):
    """交易所常量"""
    BINANCE = "BINANCE"

class Product(Enum):
    """产品类型"""
    SPOT = "SPOT"          # 现货
    PERPETUAL = "PERPETUAL"# 永续合约

class Direction(Enum):
    """交易方向"""
    LONG = "LONG"   # 买入/做多
    SHORT = "SHORT" # 卖出/做空

class Offset(Enum):
    """开平方向（现货无开平，此处兼容合约逻辑）"""
    OPEN = "OPEN"   # 开仓
    CLOSE = "CLOSE" # 平仓

class OrderType(Enum):
    """订单类型"""
    LIMIT = "LIMIT"     # 限价单
    MARKET = "MARKET"   # 市价单

# 事件类型常量
EVENT_TICK = "eTick"       # Tick行情事件
EVENT_ORDER = "eOrder"     # 订单状态事件
EVENT_TRADE = "eTrade"     # 成交回报事件

# ====================== 2. 数据对象（对标VNPY Object） ======================
@dataclass
class TickData:
    """Tick行情数据"""
    symbol: str          # 交易对（如BTCUSDT）
    exchange: Exchange   # 交易所
    product: Product     # 产品类型
    datetime: datetime   # 时间戳
    last_price: float    # 最新成交价
    bid_price_1: float   # 买一价
    ask_price_1: float   # 卖一价
    bid_volume_1: float  # 买一量
    ask_volume_1: float  # 卖一量
    volume: float = 0    # 24h成交量
    vt_symbol: str = ""  # 本地唯一标识（symbol.exchange）

    def __post_init__(self):
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"

@dataclass
class OrderData:
    """订单数据"""
    order_id: str               # 本地订单ID
    symbol: str                 # 交易对
    exchange: Exchange          # 交易所
    direction: Direction        # 方向
    offset: Offset              # 开平
    type: OrderType             # 订单类型
    price: float                # 价格
    volume: float               # 委托数量
    traded_volume: float = 0    # 已成交数量
    status: str = "SUBMITTING"  # 订单状态：SUBMITTING/SUBMITTED/FILLED/CANCELLED/FAILED
    datetime: Optional[datetime] = None   # 委托时间
    order_id_exchange: str = "" # 交易所订单ID
    vt_order_id: str = ""       # 本地唯一订单ID
    vt_symbol: str = ""         # 本地合约标识

    def __post_init__(self):
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"
        self.vt_order_id = f"{self.exchange.value}.{self.order_id}"

@dataclass
class TradeData:
    """成交数据"""
    trade_id: str              # 成交ID
    order_id: str              # 关联订单ID
    symbol: str                # 交易对
    exchange: Exchange         # 交易所
    direction: Direction       # 方向
    offset: Offset             # 开平
    price: float               # 成交价格
    volume: float              # 成交数量
    datetime: datetime         # 成交时间
    order_id_exchange: str = ""# 交易所订单ID
    vt_trade_id: str = ""      # 本地唯一成交ID
    vt_order_id: str = ""      # 本地唯一订单ID
    vt_symbol: str = ""        # 本地合约标识

    def __post_init__(self):
        self.vt_symbol = f"{self.symbol}.{self.exchange.value}"
        self.vt_order_id = f"{self.exchange.value}.{self.order_id}"
        self.vt_trade_id = f"{self.exchange.value}.{self.trade_id}"

# ====================== 3. 事件引擎（核心，对标VNPY EventEngine） ======================
class Event:
    """事件基类"""
    def __init__(self, type: str, data: Any = None):
        self.type: str = type
        self.data: Any = data

class EventEngine:
    """事件引擎（后台线程+线程安全队列）"""
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._active: bool = False
        # 设置线程为守护线程，主线程退出时自动终止
        self._thread: threading.Thread = threading.Thread(target=self._run, daemon=True)
        self._handlers: Dict[str, List[Callable]] = {}

    def _run(self):
        """事件循环（核心）"""
        while self._active:
            try:
                event: Event = self._queue.get(block=True, timeout=0.5)  # 缩短超时，加快退出
                self._process(event)
            except queue.Empty:
                continue

    def _process(self, event: Event):
        """分发事件到处理器"""
        if event.type in self._handlers:
            for handler in self._handlers[event.type]:
                # 异步执行处理器，设置为守护线程
                threading.Thread(target=handler, args=(event,), daemon=True).start()

    def register(self, event_type: str, handler: Callable):
        """注册事件处理器"""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def put(self, event: Event):
        """推送事件到队列"""
        if self._active:  # 仅在活跃时推送
            self._queue.put(event)

    def start(self):
        """启动事件引擎"""
        self._active = True
        if not self._thread.is_alive():
            self._thread.start()
        print("✅ 事件引擎已启动")

    def stop(self):
        """停止事件引擎"""
        self._active = False
        # 清空队列，加快退出
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
        print("❌ 事件引擎已停止")

# ====================== 4. 币安模拟网关（核心，修复行情格式问题） ======================
class BinanceTestnetGateway:
    """币安现货测试网网关（移除WS，改用HTTP轮询行情）"""
    def __init__(self, event_engine: EventEngine):
        self.event_engine: EventEngine = event_engine
        # 测试网配置
        self.BASE_URL = "https://testnet.binance.vision"  # 现货测试网
        
        # 新增：代理配置（替换为你的代理地址，比如Clash/SSR的本地端口）
        self.proxies = {
            'http': 'http://127.0.0.1:7890',
            'https': 'http://127.0.0.1:7890'
        }

        self.api_key: str = ""
        self.api_secret: str = ""
        self.order_id_counter: int = 10000
        self.order_map: Dict[str, OrderData] = {}
        self.exchange_order_map: Dict[str, str] = {}
        
        # 行情轮询相关
        self._polling_thread: Optional[threading.Thread] = None
        self._polling_active: bool = False
        self._subscribed_symbols: set = set()

    def _get_server_time(self) -> Optional[int]:
        """获取服务器时间，解决timestamp漂移"""
        try:
            response = requests.get(f"{self.BASE_URL}/api/v3/time", timeout=5, proxies=self.proxies)
            data = response.json()
            return data["serverTime"]
        except Exception as e:
            print(f"⚠️ 获取服务器时间失败：{e}，使用本地时间")
            return int(time.time() * 1000)

    def _sign_params(self, params: Dict[str, Any]) -> str:
        """核心修正：统一使用 urlencode 并返回完整 Query String"""
        params["timestamp"] = self._get_server_time()
        params["recvWindow"] = 20000  # 增加冗余量
        
        # 过滤空值并将所有值转为字符串（确保签名一致性）
        clean_params = {k: str(v) for k, v in params.items() if v is not None}
        
        # 严格按字母序排序
        sorted_params = sorted(clean_params.items())
        query_string = urlencode(sorted_params)
        
        # 生成签名
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        
        return f"{query_string}&signature={signature}"

    def connect(self, api_key: str, api_secret: str):
        """连接币安测试网"""
        self.api_key = api_key
        self.api_secret = api_secret
        
        # 验证连接（获取账户信息）
        try:
            self._get_account_info()
            print("✅ 币安测试网连接成功（虚拟资金账户已加载）")
        except Exception as e:
            raise RuntimeError(f"❌ 测试网连接失败：{e}")
        
        # 启动HTTP行情轮询
        self.start_ticker_polling()

    def _get_account_info(self) -> Dict:
        """获取账户信息"""
        full_query_string = self._sign_params({"recvWindow": 20000})
        url = f"{self.BASE_URL}/api/v3/account"
        headers = {"X-MBX-APIKEY": self.api_key}
        response = requests.get(url, params=full_query_string, headers=headers, timeout=5, proxies=self.proxies)
        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> Dict:
        """统一处理API响应"""
        data = response.json()
        if "code" in data and data["code"] != 0:
            raise RuntimeError(f"API错误: {data['msg']} (code: {data['code']})")
        return data

    def _poll_ticker(self):
        """轮询行情（核心：修复格式兼容问题）"""
        while self._polling_active:
            for symbol in self._subscribed_symbols:
                try:
                    # 方案1：请求单个交易对的行情（优先）
                    url = f"{self.BASE_URL}/api/v3/ticker/24hr"
                    params = {"symbol": symbol}
                    response = requests.get(url, timeout=5,proxies=self.proxies)
                    data = response.json()
                    
                    # 处理两种返回格式：字典（单交易对）/列表（所有交易对）
                    ticker_data = None
                    if isinstance(data, dict):
                        # 格式1：单个交易对字典
                        ticker_data = data
                    elif isinstance(data, list):
                        # 格式2：所有交易对列表，筛选目标symbol
                        for item in data:
                            if item.get("symbol") == symbol:
                                ticker_data = item
                                break
                    
                    # 校验数据是否有效
                    if not ticker_data or not ticker_data.get("symbol"):
                        print(f"⚠️ 未获取到{symbol}的行情数据，返回内容：{data[:200]}")
                        continue
                    
                    # 解析为TickData并推送事件
                    tick = TickData(
                        symbol=ticker_data["symbol"],
                        exchange=Exchange.BINANCE,
                        product=Product.SPOT,
                        datetime=datetime.fromtimestamp(ticker_data["closeTime"]/1000),
                        last_price=float(ticker_data["lastPrice"]),
                        bid_price_1=float(ticker_data["bidPrice"]),
                        ask_price_1=float(ticker_data["askPrice"]),
                        bid_volume_1=float(ticker_data["bidQty"]),
                        ask_volume_1=float(ticker_data["askQty"]),
                        volume=float(ticker_data["volume"])
                    )
                    self.event_engine.put(Event(EVENT_TICK, tick))
                    print(f"📈 行情更新 | {tick.symbol} | 最新价: {tick.last_price} | 买一: {tick.bid_price_1} | 卖一: {tick.ask_price_1}")
                    
                except Exception as e:
                    # 打印详细错误和返回数据，方便排查
                    try:
                        raw_data = response.text[:200] if 'response' in locals() else "无响应数据"
                    except:
                        raw_data = "无法获取响应数据"
                    print(f"⚠️ 行情轮询失败（{symbol}）：{str(e)} | 返回数据：{raw_data}")
            # 每秒轮询一次（可调整频率）
            time.sleep(1)

    def start_ticker_polling(self):
        """启动行情轮询线程"""
        self._polling_active = True
        self._polling_thread = threading.Thread(target=self._poll_ticker, daemon=True)
        self._polling_thread.start()
        print("✅ HTTP行情轮询已启动（每秒更新一次）")

    def stop_ticker_polling(self):
        """停止行情轮询"""
        self._polling_active = False
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=2)
        print("❌ HTTP行情轮询已停止")

    def subscribe(self, symbol: str):
        """订阅行情（添加到轮询列表）"""
        self._subscribed_symbols.add(symbol)
        print(f"📌 已订阅行情: {symbol}（HTTP轮询）")

    def send_order(self, order: OrderData) -> str:
        # 1. 生成本地订单ID
        local_order_id = f"LOCAL_{self.order_id_counter}"
        self.order_id_counter += 1
        order.order_id = local_order_id
        self.order_map[local_order_id] = order

        # 2. 构建订单参数
        side = "BUY" if order.direction == Direction.LONG else "SELL"
        params = {
            "symbol": order.symbol,
            "side": side,
            "type": order.type.value,
            "quantity": str(order.volume), # 转为字符串
            "recvWindow": 20000
        }
        
        # 核心修正：限价单必须包含 price 和 timeInForce
        if order.type == OrderType.LIMIT:
            params["price"] = str(order.price)
            params["timeInForce"] = "GTC"  # 补全此项

        # 3. 获取签名后的字符串并发送
        full_query_string = self._sign_params(params)
        url = f"{self.BASE_URL}/api/v3/order"
        headers = {"X-MBX-APIKEY": self.api_key}
        
        try:
            # 关键点：params 接收组装好的字符串而非字典
            response = requests.post(url, params=full_query_string, headers=headers, timeout=5, proxies=self.proxies)
            result = self._handle_response(response)
            
            # 4. 记录交易所订单ID映射
            order_id_exchange = result["orderId"]
            self.exchange_order_map[str(order_id_exchange)] = local_order_id
            
            # 5. 更新订单状态并推送事件
            order.status = "SUBMITTED"
            order.order_id_exchange = str(order_id_exchange)
            self.event_engine.put(Event(EVENT_ORDER, order))
            print(f"📤 发送订单 | {order.vt_order_id} | 方向: {side} | 价格: {order.price} | 数量: {order.volume}")
            return local_order_id
        except Exception as e:
            order.status = "FAILED"
            self.event_engine.put(Event(EVENT_ORDER, order))
            print(f"❌ 订单发送失败 | {local_order_id} | 原因: {e}")
            return local_order_id

    def cancel_order(self, vt_order_id: str):
        """撤销订单"""
        try:
            # 解析本地订单ID
            local_order_id = vt_order_id.split(".")[1]
            order = self.order_map.get(local_order_id)
            if not order or not order.order_id_exchange:
                print(f"❌ 撤销失败 | 订单不存在或未提交: {vt_order_id}")
                return

            # 构建撤单参数
            params = {
            "symbol": order.symbol,
            "orderId": order.order_id_exchange,
            "recvWindow": 20000
        }
            full_query_string = self._sign_params(params)

            url = f"{self.BASE_URL}/api/v3/order"
            headers = {"X-MBX-APIKEY": self.api_key}
            # DELETE 请求也一样
            response = requests.delete(url, params=full_query_string, headers=headers, timeout=5, proxies=self.proxies)
            self._handle_response(response)
            print(f"🔄 撤销订单 | {vt_order_id} | 交易所ID: {order.order_id_exchange}")
        except Exception as e:
            print(f"❌ 撤销失败 | {vt_order_id} | 原因: {e}")

# ====================== 5. 策略模板（对标VNPY Strategy） ======================
class BaseStrategy:
    """策略基类"""
    def __init__(self, gateway: BinanceTestnetGateway, event_engine: EventEngine, strategy_name: str):
        self.gateway = gateway
        self.event_engine = event_engine
        self.strategy_name = strategy_name
        self.vt_symbol = ""
        self.active = False

        # 注册事件处理器
        self.event_engine.register(EVENT_TICK, self._on_tick)
        self.event_engine.register(EVENT_ORDER, self._on_order)
        self.event_engine.register(EVENT_TRADE, self._on_trade)

    def _on_tick(self, event: Event):
        """Tick事件处理（内部转发）"""
        tick: TickData = event.data
        if tick.vt_symbol != self.vt_symbol or not self.active:
            return
        self.on_tick(tick)

    def _on_order(self, event: Event):
        """订单事件处理（内部转发）"""
        order: OrderData = event.data
        self.on_order(order)

    def _on_trade(self, event: Event):
        """成交事件处理（内部转发）"""
        trade: TradeData = event.data
        self.on_trade(trade)

    def on_tick(self, tick: TickData):
        """策略核心逻辑（需子类实现）"""
        pass

    def on_order(self, order: OrderData):
        """订单状态更新（可选实现）"""
        pass

    def on_trade(self, trade: TradeData):
        """成交回报处理（可选实现）"""
        pass

    def buy(self, price: float, volume: float):
        """买入订单"""
        order = OrderData(
            order_id="",
            symbol=self.vt_symbol.split(".")[0],
            exchange=Exchange.BINANCE,
            direction=Direction.LONG,
            offset=Offset.OPEN,
            type=OrderType.LIMIT,
            price=price,
            volume=volume
        )
        return self.gateway.send_order(order)

    def sell(self, price: float, volume: float):
        """卖出订单"""
        order = OrderData(
            order_id="",
            symbol=self.vt_symbol.split(".")[0],
            exchange=Exchange.BINANCE,
            direction=Direction.SHORT,
            offset=Offset.CLOSE,
            type=OrderType.LIMIT,
            price=price,
            volume=volume
        )
        return self.gateway.send_order(order)

    def start(self):
        """启动策略"""
        self.active = True
        print(f"🚀 策略启动: {self.strategy_name}")

    def stop(self):
        """停止策略"""
        self.active = False
        print(f"🛑 策略停止: {self.strategy_name}")

# ====================== 6. 示例策略：网格交易 ======================
class GridStrategy(BaseStrategy):
    """币安测试网网格策略"""
    def __init__(self, gateway: BinanceTestnetGateway, event_engine: EventEngine):
        super().__init__(gateway, event_engine, "BinanceGridStrategy")
        self.vt_symbol = "BTCUSDT.BINANCE"
        self.grid_step = 10
        self.grid_volume = 0.001
        self.last_grid_price = 0
        self.lower_price = 50000
        self.upper_price = 51000

    def on_tick(self, tick: TickData):
        """网格策略核心逻辑"""
        current_price = tick.last_price

        # 价格低于下沿 → 买入
        if current_price <= self.lower_price and self.last_grid_price != self.lower_price:
            self.buy(current_price, self.grid_volume)
            self.last_grid_price = self.lower_price
            print(f"📥 网格买入 | 价格: {current_price} | 数量: {self.grid_volume}")

        # 价格高于上沿 → 卖出
        elif current_price >= self.upper_price and self.last_grid_price != self.upper_price:
            self.sell(current_price, self.grid_volume)
            self.last_grid_price = self.upper_price
            print(f"📤 网格卖出 | 价格: {current_price} | 数量: {self.grid_volume}")

    def on_trade(self, trade: TradeData):
        """成交后打印日志"""
        print(f"📊 策略成交 | {trade.direction.value} | 价格: {trade.price} | 数量: {trade.volume}")

# ====================== 7. 主程序（启动入口） ======================
def main():
    # 加载环境变量
    load_dotenv()
    API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
    API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET")

    # 验证API密钥
    if not API_KEY or not API_SECRET:
        print("❌ 请先在.env文件中配置BINANCE_TESTNET_API_KEY和BINANCE_TESTNET_API_SECRET")
        return

    # 全局变量，供信号处理使用
    global event_engine, gateway
    # 1. 初始化事件引擎
    event_engine = EventEngine()
    event_engine.start()

    # 2. 初始化币安测试网网关
    gateway = BinanceTestnetGateway(event_engine)
    try:
        gateway.connect(API_KEY, API_SECRET)
    except RuntimeError as e:
        print(e)
        event_engine.stop()
        return

    # 3. 订阅行情
    gateway.subscribe("BTCUSDT")

    # 4. 初始化并启动策略
    strategy = GridStrategy(gateway, event_engine)
    strategy.start()

    # 5. 保持程序运行（可中断）
    print("✅ 系统启动完成，按Ctrl+C退出...")
    try:
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"⚠️ 程序异常：{e}")
    finally:
        # 安全退出
        strategy.stop()
        gateway.stop_ticker_polling()
        event_engine.stop()
        print("✅ 系统已安全停止")

if __name__ == "__main__":
    main()