import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import yfinance as yf
from lightgbm import LGBMClassifier
from sklearn.metrics import classification_report, accuracy_score, precision_score

def train_mtf_smc_lightgbm():
    print("กำลังดึงข้อมูล Bitcoin 1H ย้อนหลัง 720 วัน...")
    df = yf.download("BTC-USD", period="720d", interval="1h")
    
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.apply(pd.to_numeric, errors='coerce').dropna()

    print("กำลังสกัดโครงสร้าง SMC และ Multi-Timeframe Context...")
    
    # 1. Higher Timeframe Context (จำลองภาพใหญ่บนกราฟ 1H)
    # EMA 200 ชั่วโมง (เทียบเท่าประมาณ 8 วัน) เพื่อดูว่าเทรนด์หลักอยู่ฝั่งไหน
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    df['Macro_Trend'] = np.where(df['Close'] > df['EMA_200'], 1, -1)
    
    # 2. SMC Features (โครงสร้างระดับ 1H)
    df['FVG_Bullish'] = np.where(df['Low'] > df['High'].shift(2), 1, 0)
    df['FVG_Bearish'] = np.where(df['High'] < df['Low'].shift(2), 1, 0)
    
    df['Swing_High'] = np.where(df['High'] == df['High'].rolling(11, center=True).max(), 1, 0)
    df['Swing_Low'] = np.where(df['Low'] == df['Low'].rolling(11, center=True).min(), 1, 0)
    
    df['Last_Resistance'] = df['High'].where(df['Swing_High'] == 1).ffill()
    df['Last_Support'] = df['Low'].where(df['Swing_Low'] == 1).ffill()
    
    # 3. Confluence Indicator (การผสานพลัง)
    # บอก AI ว่าตอนนี้เกิด BOS ຂาขึ้น "ในขณะที่" เทรนด์หลักก็เป็นขาขึ้นด้วยหรือไม่
    df['BOS_Up'] = np.where((df['Close'] > df['Last_Resistance']) & (df['Close'].shift(1) <= df['Last_Resistance']), 1, 0)
    df['BOS_Down'] = np.where((df['Close'] < df['Last_Support']) & (df['Close'].shift(1) >= df['Last_Support']), 1, 0)
    
    df['Strong_Buy_Signal'] = np.where((df['BOS_Up'] == 1) & (df['Macro_Trend'] == 1), 1, 0)
    df['Trap_Buy_Signal'] = np.where((df['BOS_Up'] == 1) & (df['Macro_Trend'] == -1), 1, 0)

    # 4. Target (คงเป้าหมายเดิม 0.3% ใน 3 ชั่วโมง)
    df['Forward_3H_Return'] = df['Close'].shift(-3) / df['Close'] - 1
    df['Target'] = np.where(df['Forward_3H_Return'] > 0.003, 1, 0)

    df = df.dropna()

    # 5. Time-Series Split 80/20
    features = ['Macro_Trend', 'FVG_Bullish', 'FVG_Bearish', 'BOS_Up', 'BOS_Down', 'Strong_Buy_Signal', 'Trap_Buy_Signal']
    split_index = int(len(df) * 0.8)
    
    X_train, y_train = df[features].iloc[:split_index], df['Target'].iloc[:split_index]
    X_test, y_test = df[features].iloc[split_index:], df['Target'].iloc[split_index:]

    print("กำลังเทรนโมเดล LightGBM (MTF SMC Mode)...")
    model = LGBMClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.03,
        class_weight='balanced',
        random_state=42,
        verbose=-1
    )
    model.fit(X_train, y_train)

    # 6. คัดกรองสัญญาณ
    y_pred_proba = model.predict_proba(X_test)[:, 1]
    threshold = 0.60
    y_pred = np.where(y_pred_proba >= threshold, 1, 0)

    total_days = len(y_test) / 24
    total_trades = sum(y_pred)

    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)

    print("\n" + "="*50)
    print(" 🦅 ผลการทดสอบโมเดล SMC + ภาพใหญ่ (Macro Trend)")
    print("="*50)
    print(f"ระยะเวลา Test: {total_days:.1f} วัน")
    print(f"จำนวนครั้งที่ AI สั่งเทรด: {total_trades} ครั้ง (เฉลี่ย {total_trades/total_days:.1f} ครั้ง/วัน)")
    print(f"Precision (ทายขึ้นแล้วพุ่งจริง): {prec * 100:.2f}%")
    print("\nรายงานเชิงลึก:")
    print(classification_report(y_test, y_pred, target_names=['Sideway/Down (0)', 'Strong Up (1)'], zero_division=0))

if __name__ == "__main__":
    train_mtf_smc_lightgbm()