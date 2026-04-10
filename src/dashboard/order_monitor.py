from datetime import datetime
from queue import Empty, Queue
from tkinter import BOTH, LEFT, RIGHT, TOP, X, Y, StringVar, Tk, Toplevel, ttk

from src.infrastructure.event_engine import EventEngine
from src.infrastructure.events import EVENT_ORDER, EVENT_TICK, EVENT_TRADE, Event


class OrderMonitorGUI:
    def __init__(self, event_engine: EventEngine) -> None:
        self.event_engine = event_engine
        self.root = Tk()
        self.root.title("Algo Trading Order Monitor")
        self.root.geometry("1400x900")
        self._ui_queue: Queue[tuple[str, object]] = Queue()
        self._order_rows: dict[str, str] = {}
        self._trade_count = 0
        self._order_count = 0
        self._filled_count = 0
        self._rejected_count = 0
        self._tick_count = 0
        self.order_count_var = StringVar(value="订单: 0")
        self.trade_count_var = StringVar(value="成交: 0")
        self.filled_count_var = StringVar(value="成交完成: 0")
        self.rejected_count_var = StringVar(value="拒单: 0")
        self.tick_count_var = StringVar(value="Tick: 0")
        self._build_layout()
        self._register_handlers()
        self.root.after(120, self._drain_queue)

    def _build_layout(self) -> None:
        top_bar = ttk.Frame(self.root)
        top_bar.pack(side=TOP, fill=X, padx=8, pady=8)
        ttk.Label(top_bar, textvariable=self.order_count_var, width=16).pack(side=LEFT)
        ttk.Label(top_bar, textvariable=self.trade_count_var, width=16).pack(side=LEFT)
        ttk.Label(top_bar, textvariable=self.filled_count_var, width=18).pack(side=LEFT)
        ttk.Label(top_bar, textvariable=self.rejected_count_var, width=16).pack(side=LEFT)
        ttk.Label(top_bar, textvariable=self.tick_count_var, width=16).pack(side=LEFT)

        content = ttk.Panedwindow(self.root, orient="vertical")
        content.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))

        order_frame = ttk.Labelframe(content, text="订单监控")
        trade_frame = ttk.Labelframe(content, text="成交监控")
        tick_frame = ttk.Labelframe(content, text="Tick监控")
        log_frame = ttk.Labelframe(content, text="事件日志")
        content.add(order_frame, weight=3)
        content.add(trade_frame, weight=2)
        content.add(tick_frame, weight=2)
        content.add(log_frame, weight=2)

        self.order_table = self._create_table(
            order_frame,
            ("order_id", "strategy", "symbol", "status", "price", "volume", "traded", "reason", "time"),
            ("订单ID", "策略", "合约", "状态", "价格", "数量", "已成交", "拒单原因", "时间"),
        )
        self.trade_table = self._create_table(
            trade_frame,
            ("trade_id", "order_id", "strategy", "symbol", "price", "volume", "direction", "time"),
            ("成交ID", "订单ID", "策略", "合约", "价格", "数量", "方向", "时间"),
        )
        self.tick_table = self._create_table(
            tick_frame,
            ("symbol", "last", "bid1", "ask1", "time"),
            ("合约", "最新价", "买一", "卖一", "时间"),
        )

        log_container = ttk.Frame(log_frame)
        log_container.pack(fill=BOTH, expand=True, padx=4, pady=4)
        self.log_text = ttk.Treeview(log_container, columns=("time", "event"), show="headings", height=6)
        self.log_text.heading("time", text="时间")
        self.log_text.heading("event", text="日志")
        self.log_text.column("time", width=180, anchor="center")
        self.log_text.column("event", width=1000, anchor="w")
        log_scroll = ttk.Scrollbar(log_container, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        log_scroll.pack(side=RIGHT, fill=Y)

    def _create_table(self, parent: Toplevel | ttk.Labelframe, columns: tuple[str, ...], headings: tuple[str, ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill=BOTH, expand=True, padx=4, pady=4)
        table = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for col, heading in zip(columns, headings):
            table.heading(col, text=heading)
            table.column(col, anchor="center", width=120)
        table.column(columns[0], width=220)
        table.column(columns[-1], width=180)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        return table

    def _register_handlers(self) -> None:
        self.event_engine.register(EVENT_ORDER, self._on_order)
        self.event_engine.register(EVENT_TRADE, self._on_trade)
        self.event_engine.register(EVENT_TICK, self._on_tick)

    def _on_order(self, event: Event) -> None:
        self._ui_queue.put(("order", event.data))

    def _on_trade(self, event: Event) -> None:
        self._ui_queue.put(("trade", event.data))

    def _on_tick(self, event: Event) -> None:
        self._ui_queue.put(("tick", event.data))

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, data = self._ui_queue.get_nowait()
            except Empty:
                break
            if kind == "order":
                self._render_order(data)
            elif kind == "trade":
                self._render_trade(data)
            elif kind == "tick":
                self._render_tick(data)
        self.root.after(120, self._drain_queue)

    def _render_order(self, order: object) -> None:
        status = getattr(order, "status").value
        order_id = getattr(order, "order_id")
        values = (
            order_id,
            getattr(order, "strategy_name"),
            getattr(order, "vt_symbol"),
            status,
            f"{getattr(order, 'price')}",
            f"{getattr(order, 'volume')}",
            f"{getattr(order, 'traded_volume')}",
            getattr(order, "reject_reason"),
            getattr(order, "created_at").strftime("%H:%M:%S"),
        )
        if order_id in self._order_rows:
            self.order_table.item(self._order_rows[order_id], values=values)
        else:
            row_id = self.order_table.insert("", 0, values=values)
            self._order_rows[order_id] = row_id
            self._order_count += 1
        if status == "FILLED":
            self._filled_count += 1
        if status == "REJECTED":
            self._rejected_count += 1
        self.order_count_var.set(f"订单: {self._order_count}")
        self.filled_count_var.set(f"成交完成: {self._filled_count}")
        self.rejected_count_var.set(f"拒单: {self._rejected_count}")
        self._append_log(f"ORDER {order_id} {status}")

    def _render_trade(self, trade: object) -> None:
        values = (
            getattr(trade, "trade_id"),
            getattr(trade, "order_id"),
            getattr(trade, "strategy_name"),
            getattr(trade, "vt_symbol"),
            f"{getattr(trade, 'price')}",
            f"{getattr(trade, 'volume')}",
            getattr(trade, "direction").value,
            getattr(trade, "traded_at").strftime("%H:%M:%S"),
        )
        self.trade_table.insert("", 0, values=values)
        if len(self.trade_table.get_children()) > 400:
            self.trade_table.delete(self.trade_table.get_children()[-1])
        self._trade_count += 1
        self.trade_count_var.set(f"成交: {self._trade_count}")
        self._append_log(f"TRADE {getattr(trade, 'trade_id')} {getattr(trade, 'direction').value}")

    def _render_tick(self, tick: object) -> None:
        values = (
            getattr(tick, "vt_symbol"),
            f"{getattr(tick, 'last_price')}",
            f"{getattr(tick, 'bid_price_1')}",
            f"{getattr(tick, 'ask_price_1')}",
            getattr(tick, "datetime").strftime("%H:%M:%S"),
        )
        self.tick_table.insert("", 0, values=values)
        if len(self.tick_table.get_children()) > 300:
            self.tick_table.delete(self.tick_table.get_children()[-1])
        self._tick_count += 1
        self.tick_count_var.set(f"Tick: {self._tick_count}")

    def _append_log(self, msg: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("", 0, values=(now, msg))
        if len(self.log_text.get_children()) > 500:
            self.log_text.delete(self.log_text.get_children()[-1])

    def run(self) -> None:
        self.root.mainloop()
