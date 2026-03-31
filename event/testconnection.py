import requests
import time
import json

class BinanceProxyTicker:
    """通过公共代理API获取币安行情"""
    
    def __init__(self):
        # 使用第三方API代理（国内可访问）
        self.apis = [
            "https://api1.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
            "https://api2.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
            "https://api3.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
            "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
            "https://data.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",  # 备用域名
        ]
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
    
    def get_ticker(self):
        """获取当前最优买卖价"""
        for api_url in self.apis:
            try:
                response = requests.get(
                    api_url, 
                    headers=self.headers,
                    timeout=3,
                    verify=False  # 忽略SSL证书验证
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        'bid': float(data['bidPrice']),
                        'ask': float(data['askPrice']),
                        'mid': (float(data['bidPrice']) + float(data['askPrice'])) / 2,
                        'time': int(time.time() * 1000)
                    }
            except:
                continue
        return None

def main():
    """主函数：持续获取实时行情"""
    print("开始获取币安BTC/USDT实时行情（通过代理API）")
    print("按 Ctrl+C 停止\n")
    
    ticker = BinanceProxyTicker()
    
    try:
        while True:
            data = ticker.get_ticker()
            
            if data:
                print(f"[{time.strftime('%H:%M:%S')}] "
                      f"买一: {data['bid']:,.2f} | "
                      f"卖一: {data['ask']:,.2f} | "
                      f"中间价: {data['mid']:,.2f}")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] 获取数据失败，重试中...")
            
            time.sleep(1)  # 每秒更新一次
            
    except KeyboardInterrupt:
        print("\n程序已停止")

if __name__ == "__main__":
    # 禁用SSL警告
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    main()