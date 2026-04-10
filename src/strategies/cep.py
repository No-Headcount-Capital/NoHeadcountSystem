from src.strategies.base import BaseStrategy


class CEPEngine:
    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    def add_strategy(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.strategy_name] = strategy

    def start(self) -> None:
        for strategy in self._strategies.values():
            strategy.start()

    def stop(self) -> None:
        for strategy in self._strategies.values():
            strategy.stop()
