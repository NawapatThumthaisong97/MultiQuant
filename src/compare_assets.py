import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def compare_assets():
    print("กำลังดึงข้อมูลเปรียบเทียบ BTC และ SPY...")
    # 1. โหลดข้อมูลทั้งสองตัวพร้อมกัน
    df = yf.download("BTC-USD SPY", period="2y")['Close']
    
    # หุ้นหยุดเสาร์อาทิตย์ แต่คริปโตเทรดทุกวัน จึงต้องตัดวันหยุดออกเพื่อให้เทียบกันได้
    df = df.dropna()
    
    # 2. คำนวณผลตอบแทนรายวัน
    returns = df.pct_change().dropna()
    
    # 3. คำนวณสถิติ (ใช้ 252 วัน ซึ่งเป็นจำนวนวันเทรดเฉลี่ยของตลาดหุ้นต่อปี)
    trading_days = 252 
    cum_returns = (1 + returns).cumprod()
    
    annual_returns = returns.mean() * trading_days
    annual_vol = returns.std() * np.sqrt(trading_days)
    sharpe = annual_returns / annual_vol
    
    rolling_max = cum_returns.cummax()
    drawdown = (cum_returns / rolling_max) - 1
    max_dd = drawdown.min()
    
    # 4. สรุปผลลงตาราง
    metrics = pd.DataFrame({
        "Cumulative Return (%)": (cum_returns.iloc[-1] - 1) * 100,
        "Sharpe Ratio": sharpe,
        "Max Drawdown (%)": max_dd * 100
    }).round(2)
    
    print("\n=== ผลการเปรียบเทียบความเสี่ยงและผลตอบแทน ===")
    print(metrics)
    
    # 5. พล็อตกราฟแข่งกัน
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(cum_returns.index, cum_returns['BTC-USD'], label='Bitcoin (High Risk)', color='orange')
    ax.plot(cum_returns.index, cum_returns['SPY'], label='S&P 500 (Stable)', color='blue')
    
    ax.set_title("Growth Comparison: BTC vs S&P 500")
    ax.set_ylabel("Growth Factor (1.0 = ทุนเริ่มต้น)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    compare_assets()