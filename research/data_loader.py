import yfinance as yf
import pandas as pd
import os

def fetch_btc_data():
    ticker = 'BTC-USD'
    print(f"Fetching historical data for {ticker}...")
    
    # ดึงข้อมูลย้อนหลัง 2 ปี
    data = yf.download(ticker, period="2y")
    
    if data.empty:
        print("No data fetched. Please check the ticker symbol or your internet connection.")
        return 
    
    # สร้างโฟลเดอร์ data/raw ถ้าระบบหาไม่เจอ
    os.makedirs("data/raw", exist_ok=True)
    
    # บันทึกข้อมูลลงไฟล์ CSV
    file_path = "data/raw/btc_data.csv"
    data.to_csv(file_path)
    print(f"Success! Data saved to {file_path}")
    
    # แสดงผลข้อมูล 5 แถวสุดท้าย
    print("\nLast 5 rows of data:")
    print(data.tail())

if __name__ == "__main__":
    fetch_btc_data()
    