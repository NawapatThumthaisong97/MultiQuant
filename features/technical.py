import pandas as pd
import ta
import numpy as np

def add_technical_features(df):
    df["Date"] = pd.to_datetime(df["Date"])



    df["Close"] = pd.to_numeric(
        df["Close"],
        errors="coerce"
    )

    #log return
    df["log_return_1d"] = np.log(
    df["Close"] /
    df["Close"].shift(1)
    )
    
    #volatility
    df["volatility_20"] = (
    df["log_return_1d"]
    .rolling(20)
    .std()
    )
    
    #ADX
    adx = ta.trend.ADXIndicator(
    high=df["High"],
    low=df["Low"],
    close=df["Close"]
    )
    df["adx"] = adx.adx()
    
    #RSI
    rsi = ta.momentum.RSIIndicator(
    close=df["Close"],
    window=14
    )
    df["rsi"] = rsi.rsi()
    
    #MACD
    macd = ta.trend.MACD(
    close=df["Close"]
    )
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"] = macd.macd_diff()
    
    #BBands
    bbands = ta.volatility.BollingerBands(
    close=df["Close"]
    )
    df["bb_high"] = bbands.bollinger_hband()
    df["bb_low"] = bbands.bollinger_lband()
    df["bb_middle"] = bbands.bollinger_mavg()
    df["bb_high_dist"] = (
    df["Close"] /
    df["bb_high"]
    - 1
    )
    df["bb_low_dist"] = (
    df["Close"] /
    df["bb_low"]
    - 1
    )
    
    #EMA
    ema_20 = ta.trend.EMAIndicator(
    close=df["Close"],
    window=20
    )
    df["ema_20"] = ema_20.ema_indicator()
    
    #EMA Distance
    df["ema_distance_20"] = (
    df["Close"] /
    df["ema_20"]
    - 1
)
    
    
    #ATR
    atr = ta.volatility.AverageTrueRange(
    high=df["High"],
    low=df["Low"],
    close=df["Close"]
    )
    df["atr_14"] = atr.average_true_range()
    
    
    df.dropna(inplace=True)
    
    return df
    
def add_target(
    df,
    horizon=5
):

    df[f"future_return_{horizon}d"] = (
        df["Close"].shift(-horizon)
        /
        df["Close"]
        - 1
    )

    df["target"] = (
        df[f"future_return_{horizon}d"] > 0
    ).astype(int)

    return df