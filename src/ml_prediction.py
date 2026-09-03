import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import xgboost as xgb
from ta.trend import ADXIndicator, MACD
from ta.volatility import AverageTrueRange
from sklearn.metrics import accuracy_score, precision_score, classification_report

def train_sniper_xgboost():
    # 1. โหลดข้อมูล (คงชุดข้อมูลเดิมไว้)
    file_path = "data/processed/btc_2y_cleaned.csv"
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
    except FileNotFoundError:
        print("ไม่พบไฟล์ข้อมูล กรุณารัน data_analysis.py ก่อนครับ")
        return

    # 2. Advanced Feature Engineering (ตัวชี้วัดการเปลี่ยนเทรนด์กะทันหัน)
    # 2.1 MACD (จับการพลิกตัวของโมเมนตัม)
    macd = MACD(close=df['Close'])
    df['MACD_Line'] = macd.macd()
    df['MACD_Diff'] = macd.macd_diff() # Histogram: จับจังหวะที่แรงซื้อชนะแรงขายฉับพลัน

    # 2.2 ADX (วัดความแข็งแกร่งของเทรนด์)
    if all(col in df.columns for col in ['High', 'Low', 'Close']):
        adx = ADXIndicator(high=df['High'], low=df['Low'], close=df['Close'])
        df['ADX'] = adx.adx() # ถ้า ADX > 25 แปลว่ามีเทรนด์ชัดเจน

    # 2.3 ATR (ความผันผวนของตลาด ณ ปัจจุบัน)
    atr = AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14)
    df['ATR_14'] = atr.average_true_range()
    df['ATR_Pct'] = df['ATR_14'] / df['Close']

    # 2.4 SMC & Price Action
    df['Price_Action_Spike'] = df['Close'] / df['Close'].shift(1) - 1 # วัดแท่งกระชากรายวัน

    # 3. Dynamic Target Engineering (เป้าหมายแบบยืดหยุ่น)
    # ระยะหวังผล 3 วันข้างหน้า
    df['Forward_3D_Return'] = df['Close'].shift(-3) / df['Close'] - 1
    
    # เกณฑ์ (Threshold) จะไม่คงที่ แต่จะเท่ากับ 1.5 เท่าของ ATR ปัจจุบัน
    # ถ้าระยะนั้นตลาดผันผวนต่ำ เป้าหมายอาจจะแค่ +1% ถ้าระยะนั้นตลาดสวิงแรง เป้าหมายอาจจะต้องถึง +4%
    df['Dynamic_Threshold'] = df['ATR_Pct'] * 1.5
    
    # สั่งให้เป็น 1 ก็ต่อเมื่อกำไรในอีก 3 วัน สูงกว่าค่า Threshold แบบไดนามิก
    df['Target'] = np.where(df['Forward_3D_Return'] > df['Dynamic_Threshold'], 1, 0)

    # ตัดข้อมูลว่างทิ้ง
    df = df.dropna()

    # 4. แบ่งข้อมูล (Time-Series Split 80/20)
    features = ['MACD_Line', 'MACD_Diff', 'ADX', 'ATR_Pct', 'Price_Action_Spike']
    
    split_index = int(len(df) * 0.8)
    X_train, y_train = df[features].iloc[:split_index], df['Target'].iloc[:split_index]
    X_test, y_test = df[features].iloc[split_index:], df['Target'].iloc[split_index:]

    # 5. เทรนโมเดล XGBoost (จูนสไตล์ Sniper เน้นชัวร์)
    # เพิ่ม scale_pos_weight หากต้องการปรับสมดุล หรือปรับ max_depth ให้ตื้นลงเพื่อกันจำข้อมูลหลอก
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=3,             # ลดความลึกลงเพื่อกัน Overfitting กับข้อมูล 2 ปี
        learning_rate=0.03,      # ค่อยๆ เรียนรู้
        subsample=0.7,
        random_state=42,
        eval_metric='logloss'
    )
    model.fit(X_train, y_train)

    # 6. คัดกรอง Signal คุณภาพสูงด้วย Probability
    # แทนที่จะให้โมเดลฟันธง 0 หรือ 1 ทันที เราขอดู "ความมั่นใจ (Probability)" ของมัน
    y_pred_proba = model.predict_proba(X_test)[:, 1]
    
    # สั่งให้ยิง Signal ก็ต่อเมื่อ AI มั่นใจเกิน 65% ขึ้นไป (ปกติคือ 50%)
    high_confidence_threshold = 0.65
    y_pred_sniper = np.where(y_pred_proba >= high_confidence_threshold, 1, 0)

    acc = accuracy_score(y_test, y_pred_sniper)
    prec = precision_score(y_test, y_pred_sniper, zero_division=0)
    
    print("\n" + "="*50)
    print(" 🎯 ผลการทดสอบโมเดลระดับ Sniper (Dynamic Target)")
    print("="*50)
    print(f"จำนวนครั้งที่ AI สั่งเทรด (จากทั้งหมด {len(y_test)} วัน): {sum(y_pred_sniper)} ครั้ง")
    print(f"Precision (ยิงแล้วโดนเป้าจริง): {prec * 100:.2f}%")
    print(f"Accuracy (ความแม่นยำรวม):    {acc * 100:.2f}%")
    print("\nรายงานเชิงลึก (Sniper Mode):")
    print(classification_report(y_test, y_pred_sniper, target_names=['Sideway/Down (0)', 'Strong Up (1)'], zero_division=0))

    # 7. ดูความสำคัญของตัวแปร
    importances = model.feature_importances_
    indices = np.argsort(importances)
    plt.figure(figsize=(8, 4))
    plt.title('Feature Importances (Trend & Volatility)')
    plt.barh(range(len(indices)), importances[indices], color='darkcyan', align='center')
    plt.yticks(range(len(indices)), [features[i] for i in indices])
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    train_sniper_xgboost()