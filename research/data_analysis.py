import pandas as pd
import matplotlib.pyplot as plt
import os

def clean_and_analyze_data():
    file_path = "data/raw/btc_data.csv"
    
    print("กำลังโหลดข้อมูล...")
    # 1. โหลดข้อมูลตามปกติ
    df = pd.read_csv(file_path, index_col=0, parse_dates=True)
    
    # --- จุดที่แก้ไข ---
    # 2. บังคับแปลงข้อมูลทุกคอลัมน์ให้เป็นตัวเลข (อะไรที่ไม่ใช่ตัวเลขจะกลายเป็น NaN)
    df = df.apply(pd.to_numeric, errors='coerce')
    
    # 3. ตรวจสอบค่าว่าง (NaN) และลบออก (ลบแถวที่เป็นปัญหาทิ้งไป)
    missing_values = df.isna().sum().sum()
    if missing_values > 0:
        print(f"พบค่าว่างหรือแถว Header ซ้อนทับจำนวน {missing_values} จุด ทำการคลีนข้อมูล...")
        df = df.dropna()
    else:
        print("ข้อมูลสมบูรณ์ ไม่มีค่าว่าง")

    # 4. คำนวณผลตอบแทนรายวัน (Daily Returns)
    df['Daily_Return'] = df['Close'].pct_change()
    
    # แถวแรกสุดจะเป็น NaN เสมอ จึงต้องลบออกอีกครั้ง
    df = df.dropna()

    # 5. บันทึกข้อมูลที่ทำความสะอาดแล้ว
    os.makedirs("data/processed", exist_ok=True)
    processed_path = "data/processed/btc_2y_cleaned.csv"
    df.to_csv(processed_path)
    print(f"บันทึกข้อมูลที่พร้อมใช้งานไปที่: {processed_path}")

    # 6. พล็อตกราฟราคาเปรียบเทียบกับความผันผวน
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    
    ax1.plot(df.index, df['Close'], color='orange', label='BTC Close Price')
    ax1.set_title('Bitcoin Daily Close Price')
    ax1.set_ylabel('Price (USD)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    ax2.plot(df.index, df['Daily_Return'], color='blue', alpha=0.7, label='Daily Returns')
    ax2.set_title('Bitcoin Daily Returns (Volatility)')
    ax2.set_ylabel('% Change')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    clean_and_analyze_data()