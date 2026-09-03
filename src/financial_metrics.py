import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import skew, kurtosis

def calculate_crypto_metrics():
    # 1. โหลดข้อมูลที่ทำความสะอาดแล้ว
    file_path = "data/processed/btc_2y_cleaned.csv"
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
    except FileNotFoundError:
        print("ไม่พบไฟล์ข้อมูล กรุณารัน data_analysis.py ก่อนครับ")
        return

    returns = df['Daily_Return']
    trading_days = 365 # คริปโตเทรดทุกวัน
    
    # 2. คำนวณผลตอบแทนและ Drawdown พื้นฐาน
    cum_returns = (1 + returns).cumprod()
    annual_return = returns.mean() * trading_days
    annual_vol = returns.std() * np.sqrt(trading_days)
    
    rolling_max = cum_returns.cummax()
    drawdown = (cum_returns / rolling_max) - 1
    max_dd = drawdown.min()
    
    # 3. ตัวชี้วัดเปรียบเทียบความเสี่ยง (Risk-Adjusted Returns)
    sharpe_ratio = annual_return / annual_vol
    
    # Sortino: คิดเฉพาะวันที่ผลตอบแทนติดลบ
    negative_returns = returns[returns < 0]
    downside_vol = negative_returns.std() * np.sqrt(trading_days)
    sortino_ratio = annual_return / downside_vol if downside_vol != 0 else np.nan
    
    # Calmar: ผลตอบแทนเทียบกับจุดที่ขาดทุนหนักสุด
    calmar_ratio = annual_return / abs(max_dd) if max_dd != 0 else np.nan
    
    # 4. วิเคราะห์การกระจายตัวของหาง (Tail Risk)
    ret_skew = skew(returns)
    ret_kurt = kurtosis(returns)
    
    # 5. แสดงผลสถิติเชิงลึก
    print("\n=== รายงานตัวชี้วัดเชิงลึกสำหรับ Bitcoin ===")
    print(f"ผลตอบแทนเฉลี่ยต่อปี:     {annual_return * 100:.2f}%")
    print(f"ความผันผวนต่อปี (Risk): {annual_vol * 100:.2f}%")
    print(f"Max Drawdown:         {max_dd * 100:.2f}%")
    print("-" * 50)
    print(f"Sharpe Ratio:  {sharpe_ratio:.2f} (คะแนนรวมเมื่อมองความผันผวนคือความเสี่ยง)")
    print(f"Sortino Ratio: {sortino_ratio:.2f} (คะแนนรวมเมื่อมองเฉพาะขาลงคือความเสี่ยง)")
    print(f"Calmar Ratio:  {calmar_ratio:.2f} (ความคุ้มค่าเทียบกับจังหวะพอร์ตแตก)")
    print("-" * 50)
    print(f"Skewness:  {ret_skew:.2f} (ค่าบวก = โอกาสพุ่งแรงมีน้ำหนักมากกว่าร่วงแรง)")
    print(f"Kurtosis:  {ret_kurt:.2f} (เกิน 3.0 = ตลาดมีโอกาสเกิดความผันผวนสุดวิสัยสูงมาก)")
    print("===========================================\n")

    # 6. พล็อตกราฟกระจายตัว (Histogram) เพื่อดูว่าอารมณ์ตลาดเบ้ไปทางไหน
    plt.figure(figsize=(10, 5))
    plt.hist(returns * 100, bins=50, color='purple', alpha=0.7, edgecolor='black')
    plt.axvline(x=0, color='red', linestyle='--', linewidth=1)
    plt.title('Bitcoin Daily Returns Distribution (Fat Tails Observation)')
    plt.xlabel('Daily Return (%)')
    plt.ylabel('Frequency (Days)')
    plt.grid(True, alpha=0.3)
    plt.show()

if __name__ == "__main__":
    calculate_crypto_metrics()