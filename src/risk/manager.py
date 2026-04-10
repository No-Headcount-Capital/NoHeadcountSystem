from dataclasses import dataclass

from src.infrastructure.models import Direction, Signal


@dataclass(slots=True)
class RiskConfig:
    max_order_volume: float = 1.0
    max_symbol_position: float = 2.0


class RiskManager:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._net_positions: dict[str, float] = {}

    def validate_signal(self, signal: Signal) -> tuple[bool, str]:
        if signal.volume <= 0:
            return False, "signal.volume 必须大于 0"
        if signal.volume > self.config.max_order_volume:
            return False, f"单笔下单量超过限制: {signal.volume} > {self.config.max_order_volume}"
        signed_volume = signal.volume if signal.direction == Direction.LONG else -signal.volume
        current = self._net_positions.get(signal.vt_symbol, 0.0)
        projected = current + signed_volume
        if abs(projected) > self.config.max_symbol_position:
            return False, f"净仓位超过限制: {projected} > {self.config.max_symbol_position}"
        return True, ""

    def on_order_filled(self, vt_symbol: str, direction: Direction, volume: float) -> None:
        signed_volume = volume if direction == Direction.LONG else -volume
        self._net_positions[vt_symbol] = self._net_positions.get(vt_symbol, 0.0) + signed_volume
