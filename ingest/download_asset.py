import yfinance as yf
from pathlib import Path

DATA_DIR = Path("data")

def download_asset(
    ticker,
    filename,
    period="5y",
    interval="1d"
):
    DATA_DIR.mkdir(exist_ok=True)

    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=True
    )

    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    df.to_csv(
        DATA_DIR / f"{filename}.csv"
    )

    return df