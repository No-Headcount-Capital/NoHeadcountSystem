from datetime import date, datetime, timedelta, timezone
import numpy as np
import matplotlib.pyplot as plt
import csv
import os

from tqsdk import (
    TqApi, TqAuth, TqBacktest, BacktestFinished, tafunc, TqSim, TqKq
)

# ========= 全局配置 =========
EXCHANGE = "CFFEX"
UNIVERSE = ["IF", "IM", "IH", "IC"]
# UNIVERSE = ["IC", "IM"]

WINDOW   = 120                         # 均值/波动窗口（bar 数）
BAR_SEC  = 60                         # 1 分钟 bar
ROLL_BUFFER_DAYS = 1                  # 距到期 N 天开始换月

# 开仓 / 锁仓参数
K_OPEN   = 2.0        # |z| > K_OPEN 开第一条腿
K_LOCK   = 0.3        # 当天 |z| < K_LOCK 且有浮盈 才锁仓
LOCK_MIN_PNL_POINTS = 1   # 当前腿点数浮盈 >= 该值才允许锁仓，避免把亏损锁死

# 仓位管理参数
MAX_MARGIN_RATIO = 0.9           # 总保证金最大占用率（锁仓后的上限）
MAX_MARGIN_RATIO_OPEN = 0.70     # 开仓时的保证金限制（提高到70%，保留20%空间用于锁仓）
MAX_MARGIN_RATIO_LOCK = 0.85     # 锁仓时的保证金限制（锁仓后总共85%，留5%缓冲）
MAX_LOTS_PER_SYMBOL = 9999       # 单品种最大手数（取消限制）
MAX_POSITIONS_PER_SYMBOL = 9999  # 每个品种最多持有的独立仓位数（取消限制，只受保证金约束）
BASE_LOTS = 18                   # 基础开仓手数（z = 2.0时的默认手数）

# 信号强度与手数映射
LOTS_BY_SIGNAL = {
    3.5: 21,   # |z| >= 3.5: 21手（极强信号）
    3.0: 20,   # |z| >= 3.0: 20手（强信号）
    2.5: 19,   # |z| >= 2.5: 19手（中强信号）
    2.0: 18,   # |z| >= 2.0: 18手（基础信号）
}

START_DATE = date(2025, 6, 1)
END_DATE   = date(2025, 12, 31)

AUTH_USER = "13925448801"       
AUTH_PASS = "6741Alarmfire"

SIM_ACC = TqSim(init_balance=10000000)  # 模拟账户初始资金

# 获取当前脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ========= 工具函数 =========
def ym_to_symbol(prefix, year, month):
    yy = year % 100
    return f"{EXCHANGE}.{prefix}{yy:02d}{month:02d}"


def next_ym(year, month, n=1):
    m = month + n
    y = year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return y, m


def get_now_datetime_from_kline(klines):
    return tafunc.time_to_datetime(klines.iloc[-1]["datetime"])


def get_expire_dt(api, symbol):
    quote = api.get_quote(symbol)
    if quote.expire_datetime:
        return datetime.fromtimestamp(
            quote.expire_datetime,
            tz=timezone(timedelta(hours=8))
        )
    return None


def place_fut_order(api, symbol, direction, offset, volume):
    """
    中金所不支持市价单，这里使用五档盘口深度逐档吃单。
    direction: "BUY"/"SELL"
    offset: "OPEN"/"CLOSE"
    volume: 目标下单手数
    
    返回: 实际成交手数统计 dict
    """
    q = api.get_quote(symbol)
    
    # 统计成交情况
    fill_info = {
        'symbol': symbol,
        'direction': direction,
        'offset': offset,
        'target_volume': volume,
        'filled_volume': 0,
        'orders': []  # 每档的成交详情
    }
    
    remaining_volume = volume
    orders = []
    
    if direction == "BUY":
        # 买入：吃卖方五档盘口（ask price）
        print(f"\n[盘口] {symbol} BUY {volume}手 - 卖方五档:")
        
        # 先显示所有档位信息（诊断用）
        print(f"  五档完整信息:")
        for i in range(1, 6):
            price = getattr(q, f'ask_price{i}', None)
            depth_volume = getattr(q, f'ask_volume{i}', None)
            if price is not None and depth_volume is not None:
                print(f"    档位{i}: 价格={price:.2f}, 深度={depth_volume}手")
            else:
                print(f"    档位{i}: 无数据 (price={price}, volume={depth_volume})")
        
        # 逐档吃单
        for i in range(1, 6):
            if remaining_volume <= 0:
                print(f"  已完成目标手数，停止吃单")
                break
            
            price = getattr(q, f'ask_price{i}', None)
            depth_volume = getattr(q, f'ask_volume{i}', None)
            
            if price is None or depth_volume is None or depth_volume <= 0:
                continue
            
            # 计算本档可以成交的手数
            trade_volume = min(remaining_volume, depth_volume)
            
            print(f"  ✓ 档位{i}: 吃入{trade_volume}手 @ {price:.2f} (剩余需求{remaining_volume}手)")
            
            try:
                order = api.insert_order(
                    symbol=symbol,
                    direction=direction,
                    offset=offset,
                    volume=trade_volume,
                    limit_price=price,
                )
                orders.append(order)
                
                fill_info['orders'].append({
                    'level': i,
                    'price': price,
                    'depth': depth_volume,
                    'volume': trade_volume
                })
                fill_info['filled_volume'] += trade_volume
                remaining_volume -= trade_volume
                
            except Exception as e:
                print(f"  [ERROR] 档位{i}下单失败: {e}")
                continue
    
    else:  # SELL
        # 卖出：吃买方五档盘口（bid price）
        print(f"\n[盘口] {symbol} SELL {volume}手 - 买方五档:")
        
        # 先显示所有档位信息（诊断用）
        print(f"  五档完整信息:")
        for i in range(1, 6):
            price = getattr(q, f'bid_price{i}', None)
            depth_volume = getattr(q, f'bid_volume{i}', None)
            if price is not None and depth_volume is not None:
                print(f"    档位{i}: 价格={price:.2f}, 深度={depth_volume}手")
            else:
                print(f"    档位{i}: 无数据 (price={price}, volume={depth_volume})")
        
        # 逐档吃单
        for i in range(1, 6):
            if remaining_volume <= 0:
                print(f"  已完成目标手数，停止吃单")
                break
            
            price = getattr(q, f'bid_price{i}', None)
            depth_volume = getattr(q, f'bid_volume{i}', None)
            
            if price is None or depth_volume is None or depth_volume <= 0:
                continue
            
            # 计算本档可以成交的手数
            trade_volume = min(remaining_volume, depth_volume)
            
            print(f"  ✓ 档位{i}: 吃入{trade_volume}手 @ {price:.2f} (剩余需求{remaining_volume}手)")
            
            try:
                order = api.insert_order(
                    symbol=symbol,
                    direction=direction,
                    offset=offset,
                    volume=trade_volume,
                    limit_price=price,
                )
                orders.append(order)
                
                fill_info['orders'].append({
                    'level': i,
                    'price': price,
                    'depth': depth_volume,
                    'volume': trade_volume
                })
                fill_info['filled_volume'] += trade_volume
                remaining_volume -= trade_volume
                
            except Exception as e:
                print(f"  [ERROR] 档位{i}下单失败: {e}")
                continue
    
    # 输出成交汇总
    if fill_info['filled_volume'] > 0:
        avg_price = sum(o['price'] * o['volume'] for o in fill_info['orders']) / fill_info['filled_volume']
        print(f"[成交] {symbol} {direction} {offset}: "
              f"目标{fill_info['target_volume']}手, "
              f"实际{fill_info['filled_volume']}手 "
              f"({fill_info['filled_volume']/fill_info['target_volume']*100:.1f}%), "
              f"均价={avg_price:.2f}")
    else:
        print(f"[WARN] {symbol} {direction} {offset}: 无有效报价，未能成交")
    
    if remaining_volume > 0:
        print(f"[WARN] {symbol} 剩余{remaining_volume}手未能成交（盘口深度不足）")
    
    return orders if orders else None


def subscribe_pair(api, prefix, near_y, near_m):
    far_y, far_m = next_ym(near_y, near_m, 1)
    NEAR = ym_to_symbol(prefix, near_y, near_m)
    FAR  = ym_to_symbol(prefix, far_y, far_m)

    near_q = api.get_quote(NEAR)
    far_q  = api.get_quote(FAR)
    near_k = api.get_kline_serial(NEAR, BAR_SEC, WINDOW * 2)
    far_k  = api.get_kline_serial(FAR,  BAR_SEC, WINDOW * 2)

    return {
        "prefix": prefix,
        "NEAR": NEAR, "FAR": FAR,
        "near_q": near_q, "far_q": far_q,
        "near_k": near_k, "far_k": far_k,
        "near_y": near_y, "near_m": near_m,
        "far_y": far_y, "far_m": far_m,
    }


def calc_hold_bars(open_dt, close_dt):
    delta_sec = (close_dt - open_dt).total_seconds()
    return max(1, int(delta_sec // BAR_SEC))


def process_pending_signals(api, st, now_dt, cur_spread, z, abs_z, close_logs, event_logs, signal_delay_logs, current_tick_count):
    """
    处理待执行的信号（延时15个真实tick后执行）
    current_tick_count: 当前的tick计数（从回测开始累计的市场更新次数）
    """
    if "pending_signals" not in st:
        st["pending_signals"] = []
    
    signals_to_remove = []
    
    for signal in st["pending_signals"]:
        # 计算经过的真实tick数（市场更新次数）
        ticks_passed = current_tick_count - signal.get("trigger_tick", 0)
        
        if ticks_passed >= 15:  # 延时15个真实tick（15次市场更新）
            signal_type = signal["type"]
            
            if signal_type == "OPEN":
                # 重新检查开仓条件
                direction = signal["params"]["direction"]
                lots = signal["params"]["lots"]
                original_abs_z = signal["params"]["abs_z"]
                original_z = signal["params"].get("original_z", 0)  # 触发时的原始Z值（带符号）
                
                # 检查是否仍满足开仓条件
                # direction=1表示LONG_SPREAD(z<0), direction=-1表示SHORT_SPREAD(z>0)
                expected_z_positive = (direction == -1)  # direction=-1对应z>0
                current_z_positive = (z > 0)
                
                condition_met = abs_z > K_OPEN and (expected_z_positive == current_z_positive)
                
                # 记录延时对比日志
                signal_delay_logs.append({
                    "symbol": st["prefix"],
                    "signal_type": "OPEN",
                    "direction": "SHORT_SPREAD" if direction == -1 else "LONG_SPREAD",
                    "trigger_dt": signal["trigger_dt"],
                    "execute_dt": now_dt,
                    "trigger_z": original_z,
                    "trigger_abs_z": original_abs_z,
                    "execute_z": z,
                    "execute_abs_z": abs_z,
                    "condition_met": condition_met,
                    "executed": False,
                    "actual_lots": 0,  # 实际下单手数，初始为0
                    "target_lots": lots,  # 目标手数
                    "lots_reason": ""  # 成交手数原因
                })
                
                if condition_met:
                    # 仍满足条件，执行开仓
                    actual_lots = check_margin_available(api, lots, st, for_lock=False)
                    if actual_lots > 0:
                        # 判断成交手数原因
                        if actual_lots < lots:
                            lots_reason = f"保证金不足，目标{lots}手削减至{actual_lots}手"
                        else:
                            lots_reason = f"按设置参数成交{actual_lots}手"
                        
                        open_spread(api, st, direction, now_dt, cur_spread,
                                  event_logs, "OPEN_FIRST", actual_lots, abs_z)
                        st["last_open_spread"] = cur_spread
                        signal_delay_logs[-1]["executed"] = True
                        signal_delay_logs[-1]["actual_lots"] = actual_lots  # 记录实际手数
                        signal_delay_logs[-1]["lots_reason"] = lots_reason
                        print(f"[{now_dt}] {st['prefix']} 延时开仓执行: abs_z={abs_z:.2f} (触发时={original_abs_z:.2f}), 实际{actual_lots}手")
                    else:
                        signal_delay_logs[-1]["lots_reason"] = f"保证金不足，目标{lots}手无法成交"
                        print(f"[{now_dt}] {st['prefix']} 延时开仓失败: 保证金不足")
                else:
                    print(f"[{now_dt}] {st['prefix']} 延时开仓信号失效: abs_z={abs_z:.2f} (触发时={original_abs_z:.2f})")
                
                signals_to_remove.append(signal)
            
            elif signal_type == "LOCK":
                leg = signal["params"]["leg"]
                original_abs_z = signal["params"]["abs_z"]
                original_z = signal["params"].get("original_z", 0)
                
                # 检查leg是否还在legs列表中（可能已被平仓）
                if leg not in st["legs"]:
                    print(f"[{now_dt}] {st['prefix']} 锁仓信号失效: 原始腿已不存在")
                    signal_delay_logs.append({
                        "symbol": st["prefix"],
                        "signal_type": "LOCK",
                        "direction": "N/A",
                        "trigger_dt": signal["trigger_dt"],
                        "execute_dt": now_dt,
                        "trigger_z": original_z,
                        "trigger_abs_z": original_abs_z,
                        "execute_z": z,
                        "execute_abs_z": abs_z,
                        "condition_met": False,
                        "executed": False,
                        "actual_lots": 0,
                        "target_lots": 0,
                        "lots_reason": "原始腿已不存在"
                    })
                    signals_to_remove.append(signal)
                    continue
                
                leg_lots = leg.get("lots", 1)
                
                # 重新检查锁仓条件
                cur_pnl_points = leg["direction"] * (cur_spread - leg["open_spread"]) * leg_lots
                condition_met = abs_z < K_LOCK and cur_pnl_points >= LOCK_MIN_PNL_POINTS * leg_lots
                
                # 记录延时对比日志
                signal_delay_logs.append({
                    "symbol": st["prefix"],
                    "signal_type": "LOCK",
                    "direction": "LONG_SPREAD" if leg["direction"] == 1 else "SHORT_SPREAD",
                    "trigger_dt": signal["trigger_dt"],
                    "execute_dt": now_dt,
                    "trigger_z": original_z,
                    "trigger_abs_z": original_abs_z,
                    "execute_z": z,
                    "execute_abs_z": abs_z,
                    "condition_met": condition_met,
                    "executed": False,
                    "actual_lots": 0,
                    "target_lots": leg_lots,
                    "lots_reason": ""
                })
                
                if condition_met:
                    # 仍满足锁仓条件，执行锁仓
                    opp_dir = -leg["direction"]
                    lock_lots = check_margin_available(api, leg_lots, st, for_lock=True)
                    
                    if lock_lots > 0:
                        # 判断成交手数原因
                        if lock_lots < leg_lots:
                            lots_reason = f"保证金不足，目标{leg_lots}手削减至{lock_lots}手"
                        else:
                            lots_reason = f"按对冲手数成交{lock_lots}手"
                        
                        open_spread(api, st, opp_dir, now_dt, cur_spread,
                                  event_logs, "OPEN_LOCK", lock_lots, abs_z)
                        leg["is_locked"] = True
                        lock_leg = st["legs"][-1]
                        lock_leg["lock_pair_id"] = id(leg)
                        signal_delay_logs[-1]["executed"] = True
                        signal_delay_logs[-1]["actual_lots"] = lock_lots
                        signal_delay_logs[-1]["lots_reason"] = lots_reason
                        print(f"[{now_dt}] {st['prefix']} 延时锁仓执行: pnl={cur_pnl_points:.2f}, {lock_lots}手")
                    else:
                        # 锁仓失败，直接平仓
                        signal_delay_logs[-1]["lots_reason"] = f"保证金不足，目标{leg_lots}手无法锁仓，改为平仓"
                        print(f"[{now_dt}] {st['prefix']} 延时锁仓失败，直接平仓原始腿")
                        close_spread_leg(api, st, leg, now_dt, cur_spread,
                                       close_logs, event_logs, "lock_signal_failed_close")
                        if leg in st["legs"]:
                            st["legs"].remove(leg)
                else:
                    # 不满足锁仓条件，直接平仓
                    signal_delay_logs[-1]["lots_reason"] = f"锁仓条件不满足，改为平仓{leg_lots}手"
                    print(f"[{now_dt}] {st['prefix']} 延时锁仓条件不满足，直接平仓: abs_z={abs_z:.2f}, pnl={cur_pnl_points:.2f}")
                    close_spread_leg(api, st, leg, now_dt, cur_spread,
                                   close_logs, event_logs, "lock_signal_expired_close")
                    if leg in st["legs"]:
                        st["legs"].remove(leg)
                
                signals_to_remove.append(signal)
            
            elif signal_type == "CLOSE":
                leg = signal["params"]["leg"]
                reason = signal["params"]["reason"]
                original_abs_z = signal["params"].get("abs_z", 0)
                original_z = signal["params"].get("original_z", 0)
                
                # 检查leg是否还在legs列表中
                if leg not in st["legs"]:
                    print(f"[{now_dt}] {st['prefix']} 平仓信号失效: 腿已不存在")
                    signal_delay_logs.append({
                        "symbol": st["prefix"],
                        "signal_type": "CLOSE",
                        "direction": "N/A",
                        "trigger_dt": signal["trigger_dt"],
                        "execute_dt": now_dt,
                        "trigger_z": original_z,
                        "trigger_abs_z": original_abs_z,
                        "execute_z": z,
                        "execute_abs_z": abs_z,
                        "condition_met": False,
                        "executed": False,
                        "actual_lots": 0,
                        "target_lots": 0,
                        "lots_reason": "腿已不存在"
                    })
                    signals_to_remove.append(signal)
                    continue
                
                # 记录延时对比日志（平仓直接执行，不再判断条件）
                close_lots = leg.get("lots", 1)
                signal_delay_logs.append({
                    "symbol": st["prefix"],
                    "signal_type": "CLOSE",
                    "direction": "LONG_SPREAD" if leg["direction"] == 1 else "SHORT_SPREAD",
                    "trigger_dt": signal["trigger_dt"],
                    "execute_dt": now_dt,
                    "trigger_z": original_z,
                    "trigger_abs_z": original_abs_z,
                    "execute_z": z,
                    "execute_abs_z": abs_z,
                    "condition_met": True,  # 平仓不需要条件判断
                    "executed": True,
                    "actual_lots": close_lots,
                    "target_lots": close_lots,
                    "lots_reason": f"平仓{close_lots}手"
                })
                
                # 直接平仓，不再判断条件
                print(f"[{now_dt}] {st['prefix']} 延时平仓执行: {reason}")
                close_spread_leg(api, st, leg, now_dt, cur_spread,
                               close_logs, event_logs, reason + "_delayed")
                if leg in st["legs"]:
                    st["legs"].remove(leg)
                
                signals_to_remove.append(signal)
    
    # 移除已处理的信号
    for signal in signals_to_remove:
        st["pending_signals"].remove(signal)


def compute_drawdown(equity_list):
    """
    计算最大回撤和回撤序列:
    equity_list: [e1, e2, ..., en]
    返回:
        max_dd: float, 例如 -0.123 表示 -12.3%
        dd_series: list，同长度的回撤序列
    """
    if not equity_list:
        return 0.0, []

    arr = np.array(equity_list, dtype=float)
    peaks = np.maximum.accumulate(arr)
    dd = arr / peaks - 1.0
    max_dd = float(dd.min())
    return max_dd, dd.tolist()


# ========= spread 操作封装 =========
def open_spread(api, st, direction, now_dt, cur_spread, event_logs, tag, lots=1, abs_z=None):
    """
    direction: +1 = LONG_SPREAD  (近月多 / 远月空)
               -1 = SHORT_SPREAD (近月空 / 远月多)
    lots: 开仓手数
    tag: "OPEN_FIRST" / "OPEN_LOCK"
    abs_z: 开仓时的|Z|值（用于统计分析）
    """
    # 记录开仓前的交易ID数量，用于后续提取本次交易的手续费
    trades_before = len(SIM_ACC.get_trade())
    
    print(f"\n{'='*60}")
    print(f"[开仓] {st['prefix']} {tag} {'LONG' if direction==1 else 'SHORT'} SPREAD @ {cur_spread:.2f}, 目标{lots}手")
    print(f"{'='*60}")
    
    if direction == 1:
        place_fut_order(api, st["NEAR"], "BUY",  "OPEN", lots)
        place_fut_order(api, st["FAR"],  "SELL", "OPEN", lots)
    else:
        place_fut_order(api, st["NEAR"], "SELL", "OPEN", lots)
        place_fut_order(api, st["FAR"],  "BUY",  "OPEN", lots)

    api.wait_update()  # 回测中等撮合
    
    # 获取本次开仓的交易ID
    all_trades = SIM_ACC.get_trade()
    trade_ids = list(all_trades.keys())[trades_before:]

    leg = {
        "direction": direction,
        "open_dt":   now_dt,
        "open_day":  now_dt.date(),
        "open_spread": cur_spread,
        "open_trade_ids": trade_ids,  # 记录开仓交易ID
        "lots": lots,  # 记录手数
        "is_initial": (tag == "OPEN_FIRST"),  # 是否为原始开仓（非锁仓对冲腿）
        "is_locked": False,  # 是否已被锁仓
        "lock_pair_id": None  # 如果是锁仓腿，记录对应的原始腿ID
    }
    st["legs"].append(leg)

    print(f"\n[{now_dt}] {st['prefix']} {tag} 完成: "
          f"{'LONG' if direction==1 else 'SHORT'} SPREAD @ {cur_spread:.2f}, {lots}手")
    print(f"{'='*60}\n")

    # 事件日志：开仓或锁仓
    event_logs.append({
        "dt": now_dt,
        "symbol": st["prefix"],
        "event": tag,
        "direction": "LONG_SPREAD" if direction == 1 else "SHORT_SPREAD",
        "spread": cur_spread,
        "pnl_points": "",
        "lots": lots,
        "abs_z": abs_z  # 记录开仓时的|Z|值
    })


def close_spread_leg(api, st, leg, now_dt, cur_spread, close_logs, event_logs, reason):
    direction = leg["direction"]
    lots = leg.get("lots", 1)  # 兼容旧数据
    
    # 记录平仓前的交易ID数量
    trades_before = len(SIM_ACC.get_trade())

    print(f"\n{'='*60}")
    print(f"[平仓] {st['prefix']} {'LONG' if direction==1 else 'SHORT'} SPREAD @ {cur_spread:.2f}, {lots}手 ({reason})")
    print(f"{'='*60}")
    
    if direction == 1:
        place_fut_order(api, st["NEAR"], "SELL", "CLOSE", lots)
        place_fut_order(api, st["FAR"],  "BUY",  "CLOSE", lots)
    else:
        place_fut_order(api, st["NEAR"], "BUY",  "CLOSE", lots)
        place_fut_order(api, st["FAR"],  "SELL", "CLOSE", lots)

    api.wait_update()
    
    # 收集开仓和平仓的所有手续费（共4笔）
    all_trades = SIM_ACC.get_trade()
    total_commission = 0.0
    
    # 开仓手续费（2笔）
    for trade_id in leg.get("open_trade_ids", []):
        if trade_id in all_trades:
            total_commission += float(all_trades[trade_id].commission)
    
    # 平仓手续费（2笔）
    close_trade_ids = list(all_trades.keys())[trades_before:]
    for trade_id in close_trade_ids:
        total_commission += float(all_trades[trade_id].commission)

    # 计算价差盈亏（点数 × 手数）
    pnl_points = direction * (cur_spread - leg["open_spread"]) * lots
    
    # 转换为实际盈亏：需要考虑合约乘数
    # 股指期货：IF/IH/IC乘数300，IM乘数200
    # 这里简化处理，手续费已经是金额，pnl_points保持点数
    # 实际净盈亏需要从账户权益变化中体现
    
    hold_bars = calc_hold_bars(leg["open_dt"], now_dt)

    # 关闭一条腿的统计（round-trip）
    close_logs.append({
        "symbol": st["prefix"],
        "direction": "LONG_SPREAD" if direction == 1 else "SHORT_SPREAD",
        "open_dt": leg["open_dt"],
        "close_dt": now_dt,
        "lots": lots,
        "entry_spread": leg["open_spread"],
        "exit_spread": cur_spread,
        "pnl_points": pnl_points,
        "hold_bars": hold_bars,
        "commission": total_commission,  # 现在是完整的4笔手续费
        "reason": reason
    })

    # 事件日志：平仓
    event_logs.append({
        "dt": now_dt,
        "symbol": st["prefix"],
        "event": "CLOSE_" + reason,
        "direction": "LONG_SPREAD" if direction == 1 else "SHORT_SPREAD",
        "spread": cur_spread,
        "pnl_points": pnl_points,
        "lots": lots
    })

    st["realized_pnl_points"] += pnl_points
    st["trades"] += 1
    st["win_trades"] += int(pnl_points > 0)
    st["total_hold_bars"] += hold_bars
    st["trade_pnls"].append(pnl_points)

    print(f"\n[{now_dt}] {st['prefix']} 平仓完成: "
          f"{'LONG' if direction==1 else 'SHORT'} SPREAD @ {cur_spread:.2f}, "
          f"{lots}手, PnL={pnl_points:.2f}点, 手续费={total_commission:.2f}元 ({reason})")
    print(f"{'='*60}\n")


def calculate_lots_by_signal(abs_z):
    """根据信号强度计算开仓手数"""
    for threshold in sorted(LOTS_BY_SIGNAL.keys(), reverse=True):
        if abs_z >= threshold:
            return LOTS_BY_SIGNAL[threshold]
    return BASE_LOTS  # 默认基础手数


def check_margin_available(api, lots, st, for_lock=False):
    """检查是否有足够保证金开仓
    
    Args:
        for_lock: True=锁仓开仓（使用宽松限制90%），False=普通开仓（使用严格限制45%）
    """
    acc = SIM_ACC.get_account()
    # 总权益 = 静态权益 + 持仓盈亏（浮动盈亏）
    equity = float(acc.balance) + float(acc.position_profit)
    used_margin = float(acc.margin)
    
    # 估算单手双腿保证金（更精确的估算）
    near_price = float(st["near_q"].last_price)
    far_price = float(st["far_q"].last_price)
    
    # IF/IH/IC 乘数300，IM 乘数200
    multiplier = 200 if st["prefix"] == "IM" else 300
    margin_rate = 0.12  # 假设保证金率12%
    
    single_leg_margin = (near_price + far_price) / 2 * multiplier * margin_rate
    required_margin = single_leg_margin * 2 * lots  # 双腿
    
    # 根据是否锁仓选择不同的保证金限制
    if for_lock:
        # 锁仓时：可以用到90%保证金（确保能锁住浮盈）
        max_allowed_margin = equity * MAX_MARGIN_RATIO_LOCK
    else:
        # 普通开仓：只用45%保证金（预留45%给未来的锁仓）
        max_allowed_margin = equity * MAX_MARGIN_RATIO_OPEN
    
    # 检查是否超过最大占用率
    projected_margin = used_margin + required_margin
    
    if projected_margin > max_allowed_margin:
        # 计算能开的最大手数
        available_margin = max_allowed_margin - used_margin
        max_lots = int(available_margin / (single_leg_margin * 2))
        
        if max_lots <= 0:
            margin_pct = used_margin / equity * 100 if equity > 0 else 0
            limit_pct = (MAX_MARGIN_RATIO_LOCK if for_lock else MAX_MARGIN_RATIO_OPEN) * 100
            print(f"  [保证金检查] {st['prefix']} {'锁仓' if for_lock else '开仓'}受限: 当前{margin_pct:.1f}%, 限制{limit_pct:.0f}%")
        
        return max(0, max_lots)
    
    return lots


# ========= 初始化 =========
api = TqApi(
    account=SIM_ACC,
    backtest=TqBacktest(start_dt=START_DATE, end_dt=END_DATE),
    auth=TqAuth(AUTH_USER, AUTH_PASS),
    web_gui=True  # 回测模式也支持图形化界面
)

states = {}
for sym in UNIVERSE:
    st = subscribe_pair(api, sym, START_DATE.year, START_DATE.month)

    # legs & 锁仓状态
    st["legs"] = []          # 可以有多条腿（支持多次开仓）
    st["locked"] = False     # 是否锁仓
    st["lock_day"] = None
    st["last_open_spread"] = None  # 记录上次开仓的spread价格，避免同一价格重复开仓
    st["pending_signals"] = []    # 待执行的延时信号列表

    # 统计
    st["trades"] = 0
    st["win_trades"] = 0
    st["realized_pnl_points"] = 0.0
    st["total_hold_bars"] = 0
    st["trade_pnls"] = []

    states[sym] = st

time_points   = []
equity_curve  = []
pos_percent   = []
close_logs    = []   # 每笔平仓的统计
event_logs    = []   # 时间轴型事件日志（开仓 / 锁仓 / 平仓）
signal_delay_logs = []  # 信号延时对比日志
tick_count = 0  # 全局tick计数器（每次市场更新递增）

print("Start 4-index spread strategy backtest (insert_order + locking only when profitable, 1 lot each)")
print("使用真实Tick级延时：延时15个tick = 延时15次市场更新")

try:
    while True:
        api.wait_update()
        tick_count += 1  # 每次市场更新，tick计数+1

        # 拿任一 near_k 作为时间参考
        ref_dt = None
        for sym in UNIVERSE:
            st_ref = states[sym]
            if len(st_ref["near_k"]) >= WINDOW:
                ref_dt = get_now_datetime_from_kline(st_ref["near_k"])
                break
        if ref_dt is None:
            continue

        # ===== 换月逻辑 =====
        for sym, st in states.items():
            if len(st["near_k"]) == 0:
                continue

            now_dt = get_now_datetime_from_kline(st["near_k"])
            expire_dt = get_expire_dt(api, st["NEAR"])

            if expire_dt and now_dt >= expire_dt - timedelta(days=ROLL_BUFFER_DAYS):
                old_near = st["NEAR"]
                old_far = st["FAR"]
                
                # 平掉所有 leg
                if st["legs"]:
                    cur_spread = float(st["near_q"].last_price - st["far_q"].last_price)
                    for leg in list(st["legs"]):
                        close_spread_leg(
                            api, st, leg, now_dt, cur_spread,
                            close_logs, event_logs, reason="rollover"
                        )
                    st["legs"].clear()
                    st["locked"] = False
                    st["lock_day"] = None
                    st["last_open_spread"] = None  # 重置开仓价格记录
                    st["pending_signals"] = []  # 清空待执行信号

                # 换近月 / 次月
                st["near_y"], st["near_m"] = st["far_y"], st["far_m"]
                st["far_y"],  st["far_m"]  = next_ym(st["near_y"], st["near_m"], 1)
                st.update(subscribe_pair(api, sym, st["near_y"], st["near_m"]))
                
                print(f"\n[{now_dt}] ===== {sym} 换月 =====")
                print(f"  旧合约: {old_near} / {old_far}")
                print(f"  新合约: {st['NEAR']} / {st['FAR']}")
                print(f"  到期日: {expire_dt}")
                print(f"  新K线长度: near={len(st['near_k'])}, far={len(st['far_k'])}")
                print(f"  需要长度: {WINDOW}")

        # ===== 主策略：逐品种 =====
        for sym, st in states.items():
            if len(st["near_k"]) < WINDOW or len(st["far_k"]) < WINDOW:
                continue

            # Tick级别：每个tick都检查，不再限制只在K线变化时检查
            # 但仍然使用K线数据计算统计值（均值和标准差）
            now_dt = get_now_datetime_from_kline(st["near_k"])

            near_close = st["near_k"].close.iloc[-WINDOW:]
            far_close  = st["far_k"].close.iloc[-WINDOW:]
            spread_series = near_close - far_close

            mean = float(np.mean(spread_series))
            std  = float(np.std(spread_series))
            if std <= 1e-12:
                continue

            # 使用实时报价计算当前价差和Z值（tick级别）
            cur_spread = float(st["near_q"].last_price - st["far_q"].last_price)
            z = (cur_spread - mean) / std
            abs_z = abs(z)

            # ===== 处理待执行的延时信号（每个tick都处理）=====
            process_pending_signals(api, st, now_dt, cur_spread, z, abs_z, close_logs, event_logs, signal_delay_logs, tick_count)

            legs = st["legs"]

            # ---- 开仓判断（Tick级别）：延时15个tick后执行 ----
            if abs_z > K_OPEN:
                direction = -1 if z > 0 else 1
                
                # 多重检查避免相同价格重复下单：
                # 1. 检查是否与上次实际开仓的价格相同
                is_same_as_last_open = False
                if st["last_open_spread"] is not None:
                    if abs(cur_spread - st["last_open_spread"]) < 0.5:
                        is_same_as_last_open = True
                
                # 2. 检查待执行信号队列中是否已有相同方向且相似价格的信号
                has_pending_similar = any(
                    s["type"] == "OPEN" 
                    and s["params"]["direction"] == direction 
                    and abs(s["params"]["spread"] - cur_spread) < 0.5
                    for s in st.get("pending_signals", [])
                )
                
                # 3. 检查当前持仓中是否已有相同方向的腿在相似价格开仓
                has_open_position_similar = any(
                    leg.get("is_initial", True)  # 只检查原始开仓腿
                    and leg["direction"] == direction
                    and abs(leg["open_spread"] - cur_spread) < 0.5
                    for leg in st["legs"]
                )
                
                # 只有通过所有检查才添加信号
                if not is_same_as_last_open and not has_pending_similar and not has_open_position_similar:
                    lots = calculate_lots_by_signal(abs_z)
                    # 添加开仓信号到待执行队列
                    st["pending_signals"].append({
                        "type": "OPEN",
                        "trigger_dt": now_dt,
                        "params": {
                            "direction": direction,
                            "lots": lots,
                            "abs_z": abs_z,
                            "original_z": z,  # 记录触发时的原始Z值（带符号）
                            "spread": cur_spread
                        }
                    })
                    print(f"[{now_dt}] {st['prefix']} 开仓信号触发(Tick级,延时15tick): {'SHORT' if direction==-1 else 'LONG'} SPREAD, z={z:.2f}, spread={cur_spread:.2f}")

            # ---- 锁仓逻辑：延时15个tick后执行 ----
            for leg in legs:
                if not leg.get("is_initial", True):  # 跳过锁仓对冲腿
                    continue
                if leg.get("is_locked", False):  # 跳过已锁仓的腿
                    continue
                    
                leg_lots = leg.get("lots", 1)
                
                # T+0当天：尝试锁仓
                if now_dt.date() == leg["open_day"]:
                    cur_pnl_points = leg["direction"] * (cur_spread - leg["open_spread"]) * leg_lots
                    
                    if abs_z < K_LOCK and cur_pnl_points >= LOCK_MIN_PNL_POINTS * leg_lots:
                        # 检查是否已有该腿的锁仓信号
                        has_pending_lock = any(
                            s["type"] == "LOCK" and s["params"]["leg"] is leg
                            for s in st.get("pending_signals", [])
                        )
                        
                        if not has_pending_lock:
                            # 添加锁仓信号到待执行队列
                            st["pending_signals"].append({
                                "type": "LOCK",
                                "trigger_dt": now_dt,
                                "trigger_tick": tick_count,  # 记录触发时的tick计数
                                "params": {
                                    "leg": leg,
                                    "abs_z": abs_z,
                                    "original_z": z,  # 记录触发时的原始Z值
                                    "spread": cur_spread,
                                    "pnl": cur_pnl_points
                                }
                            })
                            print(f"[{now_dt}] {sym} 锁仓信号触发(延时15tick): leg {id(leg)}, pnl={cur_pnl_points:.2f} (tick#{tick_count})")
                            break  # 一次只处理一条腿
                
                # T+1及以后：尝试平仓（如果未锁仓）- 延时15个tick后执行
                else:
                    direction = leg["direction"]
                    want_close = ((direction == 1 and z >= 0) or (direction == -1 and z <= 0))
                    
                    if want_close and abs_z < K_LOCK:
                        # 检查是否已有该腿的平仓信号
                        has_pending_close = any(
                            s["type"] == "CLOSE" and s["params"]["leg"] is leg
                            for s in st.get("pending_signals", [])
                        )
                        
                        if not has_pending_close:
                            # 添加平仓信号到待执行队列
                            st["pending_signals"].append({
                                "type": "CLOSE",
                                "trigger_dt": now_dt,
                                "trigger_tick": tick_count,  # 记录触发时的tick计数
                                "params": {
                                    "leg": leg,
                                    "reason": "mean_revert",
                                    "abs_z": abs_z,
                                    "original_z": z  # 记录触发时的原始Z值
                                }
                            })
                            print(f"[{now_dt}] {sym} 平仓信号触发(延时15tick): leg {id(leg)} (tick#{tick_count})")
                            break  # 一次只处理一条腿

            # ---- 解锁逻辑：找已锁仓的配对腿进行解锁 ----
            for leg in list(legs):
                if not leg.get("is_initial", True):  # 跳过锁仓对冲腿本身
                    continue
                if not leg.get("is_locked", False):  # 跳过未锁仓的原始腿
                    continue
                
                # 找到这条腿的锁仓对冲腿
                lock_leg = None
                for l in legs:
                    if l.get("lock_pair_id") == id(leg):
                        lock_leg = l
                        break
                
                if lock_leg is None:
                    continue  # 找不到配对腿，跳过
                
                # T+1及以后判断是否解锁（检查锁仓腿的开仓日期）
                if now_dt.date() > lock_leg["open_day"]:
                    # 根据价差方向决定平哪条腿
                    if z > K_OPEN:
                        # 价差过高，平掉LONG方向的腿，保留SHORT腿
                        if leg["direction"] == 1:
                            # 原始腿是LONG，平原始腿，保留SHORT锁仓腿
                            close_spread_leg(
                                api, st, leg, now_dt, cur_spread,
                                close_logs, event_logs, reason="unlock_close_long_keep_short"
                            )
                            legs.remove(leg)
                            # 锁仓腿转为普通腿，继续持有
                            lock_leg["is_initial"] = True
                            lock_leg["is_locked"] = False
                            lock_leg["lock_pair_id"] = None
                            print(f"[{now_dt}] {sym} UNLOCK: closed LONG leg {id(leg)}, keep SHORT hedge")
                            break
                        else:
                            # 原始腿是SHORT，平LONG锁仓腿，保留SHORT原始腿
                            close_spread_leg(
                                api, st, lock_leg, now_dt, cur_spread,
                                close_logs, event_logs, reason="unlock_close"
                            )
                            legs.remove(lock_leg)
                            leg["is_locked"] = False  # 解除锁仓标记
                            print(f"[{now_dt}] {sym} UNLOCK: closed LONG hedge leg, keep SHORT original")
                            break
                    
                    elif z < -K_OPEN:
                        # 价差过低，平掉SHORT方向的腿，保留LONG腿
                        if leg["direction"] == -1:
                            # 原始腿是SHORT，平原始腿，保留LONG锁仓腿
                            close_spread_leg(
                                api, st, leg, now_dt, cur_spread,
                                close_logs, event_logs, reason="unlock_close_short_keep_long"
                            )
                            legs.remove(leg)
                            # 锁仓腿转为普通腿，继续持有
                            lock_leg["is_initial"] = True
                            lock_leg["is_locked"] = False
                            lock_leg["lock_pair_id"] = None
                            print(f"[{now_dt}] {sym} UNLOCK: closed SHORT leg {id(leg)}, keep LONG hedge")
                            break
                        else:
                            # 原始腿是LONG，平SHORT锁仓腿，保留LONG原始腿
                            close_spread_leg(
                                api, st, lock_leg, now_dt, cur_spread,
                                close_logs, event_logs, reason="unlock_close"
                            )
                            legs.remove(lock_leg)
                            leg["is_locked"] = False  # 解除锁仓标记
                            print(f"[{now_dt}] {sym} UNLOCK: closed SHORT hedge leg, keep LONG original")
                            break

        # ===== 记录权益 & 仓位占比 =====
        if ref_dt:
            acc = SIM_ACC.get_account()
            # 总权益 = 静态权益 + 持仓盈亏（浮动盈亏）
            equity = float(acc.balance) + float(acc.position_profit)
            used_margin = float(acc.margin)

            time_points.append(ref_dt)
            equity_curve.append(equity)

            # 计算保证金占用占总权益的比例
            pos_pct = (used_margin / equity * 100.0) if equity > 0 else 0.0
            pos_percent.append(pos_pct)

except BacktestFinished:
    print("\n========= 回测正常结束 =========")
    print(f"配置的回测区间: {START_DATE} 至 {END_DATE}")
    if time_points:
        print(f"实际记录的时间范围: {time_points[0].date()} 至 {time_points[-1].date()}")
        print(f"总记录点数: {len(time_points)}")
except KeyboardInterrupt:
    print("\n程序被用户中断")

finally:
    api.close()
    
    # ========= 诊断信息 =========
    if time_points:
        print(f"\n========= 诊断信息 =========")
        print(f"最后记录时间: {time_points[-1]}")
        for sym, st in states.items():
            print(f"{sym}: 当前合约 {st['NEAR']}/{st['FAR']}, K线长度 {len(st['near_k'])}/{len(st['far_k'])}")

    # ========= 最大回撤 =========
    max_dd, dd_series = compute_drawdown(equity_curve)
    print(f"\nOverall max drawdown: {max_dd * 100:.2f}%")

    # ========= 各品种统计 =========
    print("\n===== Per-symbol statistics (points × lots) =====")
    for sym, st in states.items():
        trades = st["trades"]
        wr = st["win_trades"] / trades if trades > 0 else 0
        avg_hold = st["total_hold_bars"] / trades if trades > 0 else 0
        pnls = st["trade_pnls"]
        mean_pnl = np.mean(pnls) if pnls else 0
        std_pnl  = np.std(pnls)  if pnls else 0
        stability = mean_pnl / std_pnl if std_pnl > 0 else 0

        print(f"{sym} | Total PnL={st['realized_pnl_points']:.2f} | Trades={trades} "
              f"| Win rate={wr:.2%} | Avg bars={avg_hold:.2f} "
              f"| Mean/trade={mean_pnl:.2f} | Stability={stability:.3f}")

    # ========= 导出平仓统计 CSV =========
    close_csv = os.path.join(SCRIPT_DIR, "spread_lock_4idx_backtest_close_logs.csv")
    os.makedirs(os.path.dirname(close_csv), exist_ok=True)  # 确保目录存在
    with open(close_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol","direction","open_dt","close_dt",
            "lots","entry_spread","exit_spread",
            "pnl_points","hold_bars","commission","reason"
        ])
        for log in close_logs:
            writer.writerow([
                log["symbol"],
                log["direction"],
                log["open_dt"],
                log["close_dt"],
                log["lots"],
                log["entry_spread"],
                log["exit_spread"],
                log["pnl_points"],
                log["hold_bars"],
                log["commission"],
                log["reason"]
            ])
    print(f"\nClose logs CSV exported: {close_csv}")

    # ========= 导出事件流水 CSV（开仓 / 锁仓 / 平仓）=========    
    event_csv = os.path.join(SCRIPT_DIR, "spread_lock_4idx_backtest_event_logs.csv")
    os.makedirs(os.path.dirname(event_csv), exist_ok=True)  # 确保目录存在
    with open(event_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "dt", "symbol", "event", "direction", "spread", "pnl_points", "lots"
        ])
        for ev in event_logs:
            writer.writerow([
                ev["dt"],
                ev["symbol"],
                ev["event"],
                ev["direction"],
                ev["spread"],
                ev.get("pnl_points", ""),
                ev.get("lots", "")
            ])
    print(f"Event logs CSV exported: {event_csv}")

    # ========= 导出信号延时对比 CSV =========
    delay_csv = os.path.join(SCRIPT_DIR, "signal_delay_comparison.csv")
    os.makedirs(os.path.dirname(delay_csv), exist_ok=True)
    with open(delay_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "signal_type", "direction", "trigger_dt", "execute_dt",
            "trigger_z", "trigger_abs_z", "execute_z", "execute_abs_z",
            "z_change", "abs_z_change", "condition_met", "executed", 
            "target_lots", "actual_lots", "lots_reason"
        ])
        for log in signal_delay_logs:
            z_change = log["execute_z"] - log["trigger_z"]
            abs_z_change = log["execute_abs_z"] - log["trigger_abs_z"]
            writer.writerow([
                log["symbol"],
                log["signal_type"],
                log["direction"],
                log["trigger_dt"],
                log["execute_dt"],
                f"{log['trigger_z']:.4f}",
                f"{log['trigger_abs_z']:.4f}",
                f"{log['execute_z']:.4f}",
                f"{log['execute_abs_z']:.4f}",
                f"{z_change:.4f}",
                f"{abs_z_change:.4f}",
                log["condition_met"],
                log["executed"],
                log.get("target_lots", 0),
                log.get("actual_lots", 0),
                log.get("lots_reason", "")
            ])
    print(f"Signal delay comparison CSV exported: {delay_csv}")

    # ========= 画图 =========
    if time_points:
        # 图1：Equity + 仓位占比
        fig, ax1 = plt.subplots(figsize=(12, 6))
        ax1.plot(time_points, equity_curve, label="Equity")
        ax1.set_ylabel("Equity")

        ax2 = ax1.twinx()
        ax2.step(time_points, pos_percent, where="post",
                 color="red", label="Position Usage (%)")
        ax2.set_ylabel("Position Usage (%)")

        ax1.set_xlabel("Time")
        fig.legend(loc="upper left")
        plt.title("Equity Curve and Position Usage (%)")
        plt.grid(True)
        plt.show()

        # 图2：回撤曲线
        if dd_series:
            fig2, ax_dd = plt.subplots(figsize=(12, 4))
            dd_pct = [d * 100 for d in dd_series]
            ax_dd.plot(time_points, dd_pct, label="Drawdown (%)")
            ax_dd.set_ylabel("Drawdown (%)")
            ax_dd.set_xlabel("Time")
            ax_dd.set_title("Equity Drawdown (%)")
            ax_dd.grid(True)
            plt.show()
        
        # 图3：开仓时|Z|分布
        open_events = [e for e in event_logs if e['event'] == 'OPEN_FIRST' and e.get('abs_z') is not None]
        if open_events:
            abs_z_values = [e['abs_z'] for e in open_events]
            fig3, ax_z = plt.subplots(figsize=(12, 5))
            ax_z.hist(abs_z_values, bins=30, alpha=0.7, color='blue', edgecolor='black')
            ax_z.axvline(K_OPEN, color='red', linestyle='--', linewidth=2, label=f'K_OPEN={K_OPEN}')
            ax_z.set_xlabel('|Z| Value')
            ax_z.set_ylabel('Frequency')
            ax_z.set_title(f'Distribution of |Z| at Open (n={len(abs_z_values)})')
            ax_z.legend()
            ax_z.grid(True, alpha=0.3)
            img_path = os.path.join(SCRIPT_DIR, "backtest_z_distribution.png")
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            plt.savefig(img_path, dpi=150, bbox_inches='tight')
            print(f"\n|Z|分布图已保存: {img_path}")
            plt.show()