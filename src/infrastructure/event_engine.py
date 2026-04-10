from collections import defaultdict
from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread

from .events import Event


HandlerType = Callable[[Event], None]


class EventEngine:
    def __init__(self) -> None:
        self._queue: Queue[Event] = Queue()
        self._active: bool = False
        self._thread: Thread = Thread(target=self._run, daemon=True)
        self._handlers: defaultdict[str, list[HandlerType]] = defaultdict(list)
        self._general_handlers: list[HandlerType] = []

    def _run(self) -> None:
        while self._active:
            try:
                event = self._queue.get(timeout=0.5)
            except Empty:
                continue
            self._process(event)

    def _process(self, event: Event) -> None:
        for handler in self._handlers[event.type]:
            handler(event)
        for handler in self._general_handlers:
            handler(event)

    def start(self) -> None:
        self._active = True
        if not self._thread.is_alive():
            self._thread = Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._active = False
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def put(self, event: Event) -> None:
        if self._active:
            self._queue.put(event)

    def register(self, event_type: str, handler: HandlerType) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unregister(self, event_type: str, handler: HandlerType) -> None:
        handlers = self._handlers[event_type]
        if handler in handlers:
            handlers.remove(handler)
        if not handlers:
            self._handlers.pop(event_type, None)

    def register_general(self, handler: HandlerType) -> None:
        if handler not in self._general_handlers:
            self._general_handlers.append(handler)
