import yfinance as yf
from pathlib import Path

DATA_DIR = Path("data")

def download_asset(ticker, filename):
    DATA_DIR.mkdir(exist_ok=True)

    df = yf.download(
        ticker,
        period="5y",
        interval="1d",
        auto_adjust=True
    )

    df.to_csv(DATA_DIR / f"{filename}.csv")

    return df