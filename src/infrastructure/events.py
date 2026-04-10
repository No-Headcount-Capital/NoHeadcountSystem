from dataclasses import dataclass
from typing import Any


EVENT_TICK = "eTick"
EVENT_SIGNAL = "eSignal"
EVENT_ORDER = "eOrder"
EVENT_TRADE = "eTrade"


@dataclass(slots=True)
class Event:
    type: str
    data: Any = None
