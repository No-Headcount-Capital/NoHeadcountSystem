import hashlib
import hmac
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime
from threading import Thread
from urllib.parse import urlencode

import requests
from requests import exceptions as req_exc

from .event_engine import EventEngine
from .events import EVENT_ORDER, EVENT_TICK, EVENT_TRADE, Event
from .models import Direction, Exchange, OrderData, OrderStatus, TickData, TradeData


class BaseGateway(ABC):
    def __init__(self, event_engine: EventEngine, exchange: Exchange) -> None:
        self.event_engine = event_engine
        self.exchange = exchange

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, symbol: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_order(self, order: OrderData) -> str:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class SimulatedGateway(BaseGateway):
    def __init__(self, event_engine: EventEngine) -> None:
        super().__init__(event_engine, Exchange.SIMULATED)

    def connect(self) -> None:
        return

    def subscribe(self, symbol: str) -> None:
        return

    def send_order(self, order: OrderData) -> str:
        order.status = OrderStatus.FILLED
        order.exchange_order_id = order.order_id
        self.event_engine.put(Event(EVENT_ORDER, order))
        trade = TradeData(
            order_id=order.order_id,
            symbol=order.symbol,
            exchange=order.exchange,
            direction=order.direction,
            offset=order.offset,
            price=order.price,
            volume=order.volume,
            strategy_name=order.strategy_name,
            signal_id=order.signal_id,
        )
        self.event_engine.put(Event(EVENT_TRADE, trade))
        return order.order_id

    def publish_tick(self, symbol: str, last_price: float) -> None:
        tick = TickData(
            symbol=symbol,
            exchange=Exchange.SIMULATED,
            datetime=datetime.utcnow(),
            last_price=last_price,
            bid_price_1=last_price - 0.5,
            ask_price_1=last_price + 0.5,
        )
        self.event_engine.put(Event(EVENT_TICK, tick))

    def close(self) -> None:
        return


class BinanceTestnetGateway(BaseGateway):
    def __init__(self, event_engine: EventEngine) -> None:
        super().__init__(event_engine, Exchange.BINANCE)
        self.base_url = "https://testnet.binance.vision"
        self.api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        self.timeout = float(os.getenv("BINANCE_HTTP_TIMEOUT", "5"))
        proxy_url = os.getenv("BINANCE_PROXY", "")
        self.proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        self.proxy_fallback_direct = os.getenv("BINANCE_PROXY_FALLBACK_DIRECT", "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }
        self.order_poll_interval = float(os.getenv("BINANCE_ORDER_POLL_INTERVAL", "1.0"))
        self._active = False
        self._symbols: set[str] = set()
        self._poller = Thread(target=self._poll_ticker, daemon=True)
        self._order_poller = Thread(target=self._poll_orders, daemon=True)
        self._tracked_orders: dict[str, OrderData] = {}
        self._reported_trade_ids: set[str] = set()

    def connect(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("缺少 BINANCE_TESTNET_API_KEY 或 BINANCE_TESTNET_API_SECRET")
        self._get_account_info()
        self._active = True
        if not self._poller.is_alive():
            self._poller = Thread(target=self._poll_ticker, daemon=True)
            self._poller.start()
        if not self._order_poller.is_alive():
            self._order_poller = Thread(target=self._poll_orders, daemon=True)
            self._order_poller.start()

    def subscribe(self, symbol: str) -> None:
        self._symbols.add(symbol.upper())

    def _server_timestamp(self) -> int:
        try:
            data = self._request("GET", "/api/v3/time")
            return int(data["serverTime"])
        except Exception:
            return int(time.time() * 1000)

    def _sign_query(self, params: dict[str, str]) -> str:
        params["timestamp"] = str(self._server_timestamp())
        params["recvWindow"] = "5000"
        payload = urlencode(sorted(params.items()))
        signature = hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}&signature={signature}"

    def _request(
        self,
        method: str,
        path: str,
        query: str | None = None,
        params: dict | None = None,
        private: bool = False,
    ) -> dict | list:
        url = f"{self.base_url}{path}"
        headers = {"User-Agent": "algo-trading-system"}
        if private:
            headers["X-MBX-APIKEY"] = self.api_key
        try:
            response = requests.request(
                method=method,
                url=url,
                params=query or params,
                headers=headers,
                timeout=self.timeout,
                proxies=self.proxies,
            )
        except (req_exc.SSLError, req_exc.ProxyError, req_exc.ConnectionError) as exc:
            if self.proxies and self.proxy_fallback_direct:
                print(f"代理请求失败，改为直连重试: {exc}")
                response = requests.request(
                    method=method,
                    url=url,
                    params=query or params,
                    headers=headers,
                    timeout=self.timeout,
                    proxies=None,
                )
            else:
                raise
        except req_exc.RequestException as exc:
            raise RuntimeError(f"请求Binance失败: {exc}") from exc
        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> dict | list:
        data = response.json()
        if isinstance(data, dict) and "code" in data and data["code"] not in (0, None):
            raise RuntimeError(f"Binance API错误: {data.get('msg', data)}")
        response.raise_for_status()
        return data

    def _get_account_info(self) -> dict:
        query = self._sign_query({})
        return self._request("GET", "/api/v3/account", query=query, private=True)

    def _query_order(self, symbol: str, exchange_order_id: str) -> dict:
        query = self._sign_query({"symbol": symbol, "orderId": exchange_order_id})
        data = self._request("GET", "/api/v3/order", query=query, private=True)
        if not isinstance(data, dict):
            raise RuntimeError(f"查询订单返回异常: {data}")
        return data

    def _query_order_trades(self, symbol: str, exchange_order_id: str) -> list[dict]:
        query = self._sign_query({"symbol": symbol, "orderId": exchange_order_id, "limit": "1000"})
        data = self._request("GET", "/api/v3/myTrades", query=query, private=True)
        if not isinstance(data, list):
            raise RuntimeError(f"查询成交返回异常: {data}")
        return [item for item in data if isinstance(item, dict)]

    def _poll_orders(self) -> None:
        while self._active:
            tracked_orders = list(self._tracked_orders.items())
            for exchange_order_id, order in tracked_orders:
                try:
                    order_info = self._query_order(order.symbol, exchange_order_id)
                except Exception:
                    continue
                status = str(order_info.get("status", "")).upper()
                executed_qty = float(order_info.get("executedQty", "0") or 0.0)
                order.traded_volume = max(order.traded_volume, executed_qty)
                if status in {"NEW", "PARTIALLY_FILLED"}:
                    if order.status != OrderStatus.SUBMITTED:
                        order.status = OrderStatus.SUBMITTED
                        self.event_engine.put(Event(EVENT_ORDER, order))
                    continue
                if status == "FILLED":
                    order.status = OrderStatus.FILLED
                    self.event_engine.put(Event(EVENT_ORDER, order))
                    try:
                        trades = self._query_order_trades(order.symbol, exchange_order_id)
                    except Exception:
                        trades = []
                    for trade in trades:
                        trade_id = str(trade.get("id", ""))
                        if not trade_id:
                            continue
                        trade_unique_id = f"{exchange_order_id}:{trade_id}"
                        if trade_unique_id in self._reported_trade_ids:
                            continue
                        trade_volume = float(trade.get("qty", "0") or 0.0)
                        trade_price = float(trade.get("price", "0") or 0.0)
                        if trade_volume <= 0 or trade_price <= 0:
                            continue
                        self._reported_trade_ids.add(trade_unique_id)
                        trade_event = TradeData(
                            order_id=order.order_id,
                            symbol=order.symbol,
                            exchange=order.exchange,
                            direction=order.direction,
                            offset=order.offset,
                            price=trade_price,
                            volume=trade_volume,
                            strategy_name=order.strategy_name,
                            signal_id=order.signal_id,
                            trade_id=f"BINANCE_{trade_id}",
                        )
                        self.event_engine.put(Event(EVENT_TRADE, trade_event))
                    self._tracked_orders.pop(exchange_order_id, None)
                    continue
                if status in {"CANCELED", "EXPIRED"}:
                    order.status = OrderStatus.CANCELLED
                    self.event_engine.put(Event(EVENT_ORDER, order))
                    self._tracked_orders.pop(exchange_order_id, None)
                    continue
                if status == "REJECTED":
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = "binance_order_rejected"
                    self.event_engine.put(Event(EVENT_ORDER, order))
                    self._tracked_orders.pop(exchange_order_id, None)
            time.sleep(self.order_poll_interval)

    def _poll_ticker(self) -> None:
        while self._active:
            for symbol in list(self._symbols):
                try:
                    data = self._request("GET", "/api/v3/ticker/bookTicker", params={"symbol": symbol})
                    bid = float(data["bidPrice"])
                    ask = float(data["askPrice"])
                    tick = TickData(
                        symbol=symbol,
                        exchange=Exchange.BINANCE,
                        datetime=datetime.utcnow(),
                        last_price=(bid + ask) / 2,
                        bid_price_1=bid,
                        ask_price_1=ask,
                    )
                    self.event_engine.put(Event(EVENT_TICK, tick))
                except Exception:
                    continue
            time.sleep(1.0)

    def send_order(self, order: OrderData) -> str:
        side = "BUY" if order.direction == Direction.LONG else "SELL"
        params = {
            "symbol": order.symbol,
            "side": side,
            "type": order.order_type.value,
            "quantity": str(order.volume),
        }
        if order.order_type.value == "LIMIT":
            params["price"] = str(order.price)
            params["timeInForce"] = "GTC"
        query = self._sign_query(params)
        try:
            data = self._request("POST", "/api/v3/order", query=query, private=True)
            order.status = OrderStatus.SUBMITTED
            order.exchange_order_id = str(data.get("orderId", ""))
            if order.exchange_order_id:
                self._tracked_orders[order.exchange_order_id] = order
        except Exception as exc:
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(exc)
        self.event_engine.put(Event(EVENT_ORDER, order))
        return order.order_id

    def close(self) -> None:
        self._active = False
