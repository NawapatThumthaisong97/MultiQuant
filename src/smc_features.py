import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os

def calculate_fvg(df):
    """ตรวจหา Fair Value Gap (FVG) / ช่องว่างราคารายใหญ่"""
    df['FVG_Bullish'] = np.where(df['Low'] > df['High'].shift(2), 1, 0)
    df['FVG_Bearish'] = np.where(df['High'] < df['Low'].shift(2), 1, 0)
    return df

def calculate_swing_fractals(df, window=5):
    """หาจุด Swing High / Low เพื่อสร้างแนวรับ-แนวต้าน"""
    df['Rolling_Max'] = df['High'].rolling(window=window*2+1, center=True).max()
    df['Swing_High'] = np.where(df['High'] == df['Rolling_Max'], 1, 0)
    
    df['Rolling_Min'] = df['Low'].rolling(window=window*2+1, center=True).min()
    df['Swing_Low'] = np.where(df['Low'] == df['Rolling_Min'], 1, 0)
    
    df['Last_Resistance'] = df['High'].where(df['Swing_High'] == 1).ffill()
    df['Last_Support'] = df['Low'].where(df['Swing_Low'] == 1).ffill()
    
    df.drop(['Rolling_Max', 'Rolling_Min'], axis=1, inplace=True)
    return df

def calculate_volume_profile_poc(df, bins=50):
    """หา Point of Control (POC) โซนราคาที่มีการซื้อขายมากที่สุด"""
    price_bins = np.linspace(df['Low'].min(), df['High'].max(), bins)
    volume_by_price = np.zeros(bins - 1)
    
    for i in range(len(df)):
        low, high, vol = df['Low'].iloc[i], df['High'].iloc[i], df['Volume'].iloc[i]
        for j in range(len(price_bins) - 1):
            if low <= price_bins[j+1] and high >= price_bins[j]:
                volume_by_price[j] += vol
                
    poc_index = np.argmax(volume_by_price)
    poc_price = (price_bins[poc_index] + price_bins[poc_index + 1]) / 2
    return poc_price

def extract_and_report_smc():
    file_path = "data/processed/btc_2y_cleaned.csv"
    
    # 1. โหลดข้อมูล
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
    except FileNotFoundError:
        print("ไม่พบไฟล์ข้อมูล กรุณารัน data_analysis.py เพื่อสร้างข้อมูลก่อนครับ")
        return

    # จำลอง Volume หากข้อมูลต้นทางไม่มี
    if 'Volume' not in df.columns:
        df['Volume'] = np.random.randint(100, 1000, size=len(df))
    if 'High' not in df.columns or 'Low' not in df.columns:
        print("ข้อมูลไม่มี High/Low ไม่สามารถคำนวณ SMC ได้ครับ")
        return

    # 2. คำนวณฟีเจอร์ SMC
    df = calculate_fvg(df)
    df = calculate_swing_fractals(df, window=5)
    poc_price = calculate_volume_profile_poc(df)

    # คำนวณ Break of Structure (BOS)
    df['Break_Resistance'] = np.where((df['Close'] > df['Last_Resistance']) & (df['Close'].shift(1) <= df['Last_Resistance']), 1, 0)
    df['Break_Support'] = np.where((df['Close'] < df['Last_Support']) & (df['Close'].shift(1) >= df['Last_Support']), 1, 0)

    # 3. สรุปผลลัพธ์ (พ่นข้อมูลออกมาตามรีเควสต์)
    bullish_fvg_count = df['FVG_Bullish'].sum()
    bearish_fvg_count = df['FVG_Bearish'].sum()
    bos_up_count = df['Break_Resistance'].sum()
    bos_down_count = df['Break_Support'].sum()
    current_resistance = df['Last_Resistance'].iloc[-1]
    current_support = df['Last_Support'].iloc[-1]

    print("\n" + "="*55)
    print(" 📊 สรุปข้อมูล Smart Money Concepts (SMC) ที่ตรวจพบ ")
    print("="*55)
    
    print(f"🔹 1. Fair Value Gap (ช่องว่างราคารายใหญ่):")
    print(f"   - พบ Bullish FVG (แรงซื้อกระชากทิ้งช่องว่าง): {bullish_fvg_count} จุด")
    print(f"   - พบ Bearish FVG (แรงขายกระชากทิ้งช่องว่าง): {bearish_fvg_count} จุด")
    
    print(f"\n🔹 2. Volume Profile (พฤติกรรมสะสมวอลุ่ม):")
    print(f"   - Point of Control (POC): ราคาประมาณ ${poc_price:,.2f}")
    print(f"     (นี่คือโซนราคาที่มีคนติดดอย หรือรอซื้อมากที่สุด ถือเป็นแม่เหล็กดึงดูดราคา)")

    print(f"\n🔹 3. Market Structure (โครงสร้างตลาด):")
    print(f"   - แนวต้านล่าสุด (Last Resistance): ${current_resistance:,.2f}")
    print(f"   - แนวรับล่าสุด (Last Support): ${current_support:,.2f}")
    print(f"   - จำนวนครั้งที่ราคาทะลุแนวต้าน (BOS ขาขึ้น): {bos_up_count} ครั้ง")
    print(f"   - จำนวนครั้งที่ราคาทะลุแนวรับ (BOS ขาลง): {bos_down_count} ครั้ง")
    print("="*55 + "\n")

    # 4. พล็อตกราฟซูมดู 100 วันล่าสุด
    recent_df = df.iloc[-100:]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(recent_df.index, recent_df['Close'], color='black', alpha=0.5, label='BTC Price')
    
    ax.step(recent_df.index, recent_df['Last_Resistance'], color='red', alpha=0.3, label='Resistance', where='post')
    ax.step(recent_df.index, recent_df['Last_Support'], color='green', alpha=0.3, label='Support', where='post')
    
    fvg_b = recent_df[recent_df['FVG_Bullish'] == 1]
    ax.scatter(fvg_b.index, fvg_b['Low'], marker='^', color='green', s=100, label='Bullish FVG')
    
    ax.axhline(y=poc_price, color='purple', linestyle='--', linewidth=2, label=f'POC ({poc_price:,.0f})')
    
    ax.set_title('SMC & Market Structure (Last 100 Days)')
    ax.legend()
    plt.grid(alpha=0.2)
    plt.show()

if __name__ == "__main__":
    extract_and_report_smc()