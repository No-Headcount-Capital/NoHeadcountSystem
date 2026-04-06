# test_key.py
from dotenv import load_dotenv
import os

# 加载.env文件（必须放在最前面）
load_dotenv()

# 读取密钥（用你配置的键名）
api_key = os.getenv("BINANCE_TESTNET_API_KEY")
api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

# 验证是否读取到密钥（通用方式，不硬编码）
if api_key and api_secret:
    print("✅ .env文件配置成功！已读取到密钥")
    # 仅打印前8位，避免泄露，同时验证读取正确
    print(f"API Key前8位：{api_key[:8]}...")
    print(f"Secret Key前8位：{api_secret[:8]}...")
else:
    print("❌ 未读取到密钥，请检查：")
    print("1. .env文件是否和test_key.py在同一目录")
    print("2. .env文件格式是否正确（等号无空格、无引号）")
    print("3. 键名是否为 BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET")