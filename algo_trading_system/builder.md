你现在是一个量化交易系统架构师，请基于以下架构实现Python版本：

【架构】
# 算法交易系统设计文档（CEP + 事件驱动 + vn.py架构）

---

## 1. 系统目标与关键原则
在algo_trading_system文件夹里构建一个**事件驱动的算法交易系统**，具备以下特性：

* 支持 Binance 测试网交易
* 支持模拟交易扩展
* 支持策略驱动（Strategy-based）
* 支持实时行情驱动（Tick-driven）
---
### 1.1 关键原则（必须遵守）

* CEP 是系统核心
* Strategy 只负责“产生信号”
* OMS 负责“执行”
* RMS 负责“风控”
* Gateway 只负责“与交易所通信”

---

## 2. 当前系统架构（基于代码）
系统参考的代码脚本路径：D:\360MoveData\Users\Lenovo\Desktop\cuhksz\courses\演算法交易\algo_trading\algo_trading_system\trade.py
系统核心由以下模块构成：

---

### 2.1 事件引擎（EventEngine）✅ 核心

#### 代码位置

`EventEngine` 类 

#### 职责

* 作为系统**中央消息总线**
* 所有模块通过事件通信
* 使用线程 + 队列实现异步处理

#### 工作机制

```text
Event → Queue → EventEngine → Handler（多线程分发）
```

#### 支持事件类型

* eTick（行情）
* eOrder（订单）
* eTrade（成交）

---

### 2.2 数据对象（Data Model）✅

#### 包括

* TickData（行情）
* OrderData（订单）
* TradeData（成交）

#### 特点

* 对标 vn.py 的数据结构
* 统一 vt_symbol / vt_order_id 标识

---

### 2.3 网关层（Gateway）✅

#### 当前实现

* BinanceTestnetGateway

#### 职责

1. 连接 Binance 测试网
2. 获取行情（HTTP轮询）
3. 发送订单
4. 撤单
5. 推送事件到 EventEngine

#### 数据流

```text
Binance API → Gateway → EventEngine → Strategy
```

#### 当前特点（重要）

* 行情通过 HTTP 轮询（非WebSocket）
* 订单直接由 Strategy 调用 gateway.send_order()

---

### 2.4 策略模块（Strategy）✅

#### 当前实现

* BaseStrategy
* GridStrategy

#### 职责

* 监听 Tick 事件
* 执行交易逻辑
* 生成交易行为（买/卖）

#### 当前调用方式（关键问题）

```python
self.gateway.send_order(...)
```

👉 **策略直接下单（当前设计缺陷）**

---

### 2.5 主程序（Main Loop）✅

#### 职责

* 初始化 EventEngine
* 初始化 Gateway
* 启动策略
* 保持系统运行

---

## 3. 当前系统数据流（真实运行路径）

---

### 3.1 行情驱动流程

```text
Binance
→ Gateway（HTTP轮询）
→ EventEngine.put(eTick)
→ Strategy.on_tick()
```

---

### 3.2 策略执行流程

```text
Tick
→ Strategy.on_tick()
→ 生成交易逻辑
→ 直接调用 gateway.send_order()
```

---

### 3.3 下单流程（当前）

```text
Strategy
→ Gateway.send_order()
→ Binance
→ 返回订单状态
→ EventEngine（eOrder）
```

---

### 3.4 系统闭环

```text
Market Data → Strategy → Order → Exchange → Feedback → Strategy
```

---

## 4. 当前架构问题（必须解决）

---

### ❗ 问题1：策略直接控制执行（严重耦合）

当前：

```text
Strategy → Gateway
```

问题：

* 无法统一风控
* 无法扩展多策略
* 无法统一订单管理

---

### ❗ 问题2：缺少 OMS（订单管理系统）

没有：

* 订单生命周期管理
* 订单路由层

---

### ❗ 问题3：缺少 RMS（风控系统）

没有：

* 仓位限制
* 风险控制
* 下单校验

---

### ❗ 问题4：CEP 未显式建模

当前 CEP = Strategy

👉 但正确应该是：

```text
CEP = Strategy + State + Risk + Signal
```

---

## 5. 目标架构（基于 PPT + vn.py）

---

### 5.1 核心思想

👉 系统应重构为：

```text
Event → CEP → Signal → OMS → RMS → Gateway → Exchange
```

---

## 6. 目标模块设计

---

### 6.1 CEP 引擎（核心）

#### 组成

* Strategy（策略）
* Maths Calc（指标）
* State Management（状态）
* Strategy-level RMS

#### 职责

* 处理 Tick
* 生成 Signal（信号）

---

### 6.2 Signal（新增层）

#### 职责

* 表示交易意图（不是订单）

例如：

```text
BUY BTCUSDT @ 50000
SELL BTCUSDT @ 51000
```

---

### 6.3 OMS（订单管理系统）【必须新增】

#### 职责

* 接收 Signal
* 转换为 OrderData
* 管理订单生命周期

---

### 6.4 RMS（风控系统）【必须新增】

#### 两层结构

##### （1）策略级（CEP内部）

* 防止异常信号

##### （2）全局风控（OMS前）

* 仓位限制
* 最大下单量
* 风险校验

---

### 6.5 Gateway（保持）

* BinanceTestnetGateway
* 后续扩展：

  * Binance WebSocket
  * Mock Gateway

---

## 7. 目标数据流（最终形态）

---

### 7.1 行情流

```text
Exchange
→ Gateway
→ EventEngine
→ CEP（Strategy）
```

---

### 7.2 决策流

```text
Tick
→ Strategy
→ Signal
```

---

### 7.3 执行流（关键重构）

```text
Signal
→ OMS
→ RMS
→ Gateway
→ Exchange
```

---

### 7.4 反馈流

```text
Exchange
→ Gateway
→ EventEngine
→ Strategy（on_order / on_trade）
```

---

### 7.5 完整闭环

```text
Market → CEP → Signal → OMS → Exchange → Feedback → CEP
```

---

## 8. 与当前代码的映射关系

| 模块           | 当前状态   |
| ------------ | ------ |
| EventEngine  | ✅ 已完成  |
| Data Model   | ✅ 已完成  |
| Gateway      | ✅ 已完成  |
| Strategy     | ✅ 已完成  |
| CEP Engine   | ⚠️ 未抽象 |
| Signal Layer | ❌ 缺失   |
| OMS          | ❌ 缺失   |
| RMS          | ❌ 缺失   |

---

## 9. 必须进行的重构

---

### 🔥 任务1：拆分 Strategy 与 Order

当前：

```python
strategy.buy() → gateway.send_order()
```

必须改为：

```text
Strategy → Signal → OMS → Gateway
```

---

### 🔥 任务2：实现 OMS

* OrderManager 类
* 接收 Signal
* 生成 OrderData
* 调用 Gateway

---

### 🔥 任务3：实现 RMS

* 风控规则模块
* 集成在 OMS 前

---

### 🔥 任务4：引入 Signal 类

例如：

```python
class Signal:
    symbol
    direction
    price
    volume
```

---

## 10. 开发约束

* 必须使用事件驱动（EventEngine）
* 禁止 Strategy 直接调用 Gateway
* 所有模块解耦
* 支持未来扩展：

  * 多策略
  * 多交易所
  * 回测系统

---

## 11. 开发任务

请基于当前代码实现：

1. Signal 数据结构
2. OMS（OrderManager）
3. RMS（RiskManager）
4. 重构 Strategy（不再直接下单）
5. 完整事件流改造

---


