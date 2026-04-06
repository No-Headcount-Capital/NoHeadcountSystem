import requests
import hmac
import hashlib
import time
import os
from urllib.parse import urlencode
# ====================== 配置你的测试网密钥 ======================
API_KEY="gNNo0hxUunLSBv8XOM3fg4DVwPgAtaxEueQmZ7tOdjOWfPb8iQUuNIHBpVXL2Tgc"      # 替换为你的Key
API_SECRET="8h2k3AAZEfYcvCTM59kYHhal0N2OcDG0WQGwTXaBoFuVFbTB2jCPpwdc3OMFIEhJ" # 替换为你的Secret
BASE_URL = "https://testnet.binance.vision"  # 测试网地址（主网是https://api.binance.com）

# ====================== 核心签名函数（和币安规范完全一致） ======================
def sign_params(params: dict, api_secret: str) -> str:
    """生成签名（强制用服务器时间+严格参数排序）"""
    # 1. 校验密钥非空
    if not api_secret:
        raise ValueError("API_SECRET 不能为空！")
    # 2. 过滤空值 + 按key字母排序（币安要求严格排序）
    valid_params = {k: v for k, v in params.items() if v is not None and v != ""}
    sorted_params = sorted(valid_params.items(), key=lambda x: x[0])
    # 3. 拼接为URL编码字符串（避免特殊字符）
    query_string = "&".join([f"{k}={v}" for k, v in sorted_params])
    # 4. HMAC-SHA256签名
    return hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
# ====================== 测试1：获取服务器时间（无签名，验证网络） ======================
def test_server_time():
    """测试能否连接币安服务器"""
    try:
        resp = requests.get(f"{BASE_URL}/api/v3/time", timeout=5)
        resp.raise_for_status()
        server_time = resp.json()["serverTime"]
        local_time = int(time.time() * 1000)
        time_diff = abs(server_time - local_time)
        print(f"✅ 服务器时间获取成功：{server_time}")
        print(f"✅ 本地时间：{local_time} | 时间差：{time_diff}ms")
        if time_diff > 500:
            print(f"⚠️ 时间差超过500ms，可能导致签名错误！")
        return server_time
    except Exception as e:
        print(f"❌ 服务器时间获取失败：{e}")
        return None

# ====================== 测试2：获取账户信息（需要签名，验证密钥） ======================
def test_account_info(api_key, api_secret):
    """
    使用更稳健的签名和请求流程
    """
    # 1. 获取服务器时间
    try:
        resp = requests.get(f"{BASE_URL}/api/v3/time", timeout=5)
        server_time = resp.json()["serverTime"]
    except Exception as e:
        print(f"❌ 获取服务器时间失败: {e}")
        return False

    # 2. 构建基础参数（不含签名）
    # 注意：这里不需要手动排序，保持一个固定的顺序即可
    params = {
        "recvWindow": 20000,
        "timestamp": server_time
    }
    
    # 3. 生成查询字符串 (Query String)
    query_string = urlencode(params)
    
    # 4. 对该字符串进行签名
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    # 5. 拼接最终的完整请求字符串（signature 必须在最后）
    final_query_string = f"{query_string}&signature={signature}"
    
    headers = {"X-MBX-APIKEY": api_key}
    
    try:
        # 直接把拼接好的字符串传给 params，避免 requests 重新排序
        resp = requests.get(
            f"{BASE_URL}/api/v3/account",
            params=final_query_string, 
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        print("✅ 密钥验证成功！")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"❌ 错误详情：{resp.json()}")
        return False

# ====================== 测试3：尝试发送测试订单（验证下单权限） ======================
def test_send_test_order(api_key: str, api_secret: str):
    """发送测试订单（修复后的稳健版本）"""
    server_time = test_server_time()
    if not server_time:
        return False
    
    # 1. 核心改进：所有数值类型建议先转为精确的字符串，避免浮点数精度干扰
    params = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "price": "63000",       # 改为字符串
        "quantity": "0.001",    # 改为字符串
        "timeInForce": "GTC",
        "timestamp": server_time,
        "recvWindow": 5000
    }
    
    # 2. 按照字母顺序排序并进行 URL 编码
    # urlencode 会处理好所有的转义字符
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    query_string = urlencode(sorted_params)
    
    # 3. 生成签名
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    # 4. 构建最终带签名的完整 Payload
    final_payload = f"{query_string}&signature={signature}"
    
    headers = {"X-MBX-APIKEY": api_key}
    try:
        # 注意：这里我们将 final_payload 传给 params，
        # requests 会将其原样拼接到 URL 后面
        resp = requests.post(
            f"{BASE_URL}/api/v3/order/test",
            params=final_payload, 
            headers=headers,
            timeout=5
        )
        resp.raise_for_status()
        print("✅ 测试订单发送成功！密钥和签名逻辑均有效")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"❌ 测试订单失败：{resp.json()['msg']} (code: {resp.json()['code']})")
        return False

# ====================== 执行测试 ======================
if __name__ == "__main__":
    print("===== 第一步：测试服务器连接 =====")
    test_server_time()
    
    print("\n===== 第二步：验证密钥有效性 =====")
    is_valid = test_account_info(API_KEY, API_SECRET)
    
    if is_valid:
        print("\n===== 第三步：测试下单签名 =====")
        test_send_test_order(API_KEY, API_SECRET)
    else:
        print("\n❌ 密钥无效，无需测试下单")