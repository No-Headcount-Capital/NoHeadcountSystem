import requests

# 替换为你的 Clash 端口（默认7890）
proxies = {
    'http': 'http://127.0.0.1:7897',
    'https': 'http://127.0.0.1:7897'
}

# 测试访问币安测试网
try:
    response = requests.get(
        "https://testnet.binance.vision/api/v3/ping",
        proxies=proxies,
        timeout=5
    )
    print("代理测试结果：", response.json())  # 返回 {} 则生效
    # 测试获取出口 IP
    ip_response = requests.get("https://api.ipify.org", proxies=proxies, timeout=5)
    print("当前出口 IP：", ip_response.text)  # 应显示 142.249.36.182
except Exception as e:
    print("代理测试失败：", e)
