# NoHeadcountSystem

High Frequency Trading (HFT) System for Futures and Cryptocurrencies.

## Project Structure

This project uses `uv` for dependency and environment management.

### Modules

- **Infrastructure** (`src/infrastructure/`): Exchange connectivity, data adapters, and order execution interfaces.
- **Strategies** (`src/strategies/`): Quantitative trading strategies and signal generation logic.
- **Backtest** (`src/backtest/`): Historical data simulation and strategy performance evaluation framework.
- **Risk Management** (`src/risk/`): Real-time risk control, position limits, and capitalization management.
- **Dashboard** (`src/dashboard/`): Graphical User Interface (GUI) for monitoring and control.

### Directory Layout

- `src/`: Source code modules.
- `tests/`: Unit and integration tests.
- `notebooks/`: Jupyter notebooks for research and prototyping.
- `config/`: Configuration files (YAML/JSON).
- `data/`: Local storage for historical data (ignored by git).
- `logs/`: Application logs (ignored by git).

## Getting Started

1. Install `uv`:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Sync dependencies:
   ```bash
   uv sync
   ```

3. Run the application:
   ```bash
   uv run src/main.py
   ```
