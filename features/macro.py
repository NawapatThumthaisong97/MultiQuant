import pandas as pd
import numpy as np


def merge_macro_assets(
    gold_df,
    dxy_df,
    vix_df,
    us10y_df
):
    """
    Merge Gold + DXY + VIX + US10Y
    """

    for df in [gold_df, dxy_df, vix_df, us10y_df]:
        df["Date"] = pd.to_datetime(df["Date"])

    dxy_df = dxy_df.rename(
        columns={"Close": "dxy_close"}
    )

    vix_df = vix_df.rename(
        columns={"Close": "vix_close"}
    )

    us10y_df = us10y_df.rename(
        columns={"Close": "us10y_close"}
    )

    merged = gold_df.merge(
        dxy_df[["Date", "dxy_close"]],
        on="Date",
        how="left"
    )

    merged = merged.merge(
        vix_df[["Date", "vix_close"]],
        on="Date",
        how="left"
    )

    merged = merged.merge(
        us10y_df[["Date", "us10y_close"]],
        on="Date",
        how="left"
    )

    return merged


def add_macro_features(df):

    # ---------- DXY ----------
    df["dxy_return"] = np.log(
        df["dxy_close"]
        /
        df["dxy_close"].shift(1)
    )

    # ---------- VIX ----------
    df["vix_return"] = np.log(
        df["vix_close"]
        /
        df["vix_close"].shift(1)
    )

    # ---------- US10Y ----------
    df["us10y_return"] = np.log(
        df["us10y_close"]
        /
        df["us10y_close"].shift(1)
    )

    # ---------- Gold Return ----------
    df["gold_return"] = np.log(
        df["Close"]
        /
        df["Close"].shift(1)
    )

    # ---------- Rolling Correlation ----------

    df["gold_dxy_corr_20"] = (
        df["gold_return"]
        .rolling(20)
        .corr(df["dxy_return"])
    )

    df["gold_vix_corr_20"] = (
        df["gold_return"]
        .rolling(20)
        .corr(df["vix_return"])
    )

    df["gold_us10y_corr_20"] = (
        df["gold_return"]
        .rolling(20)
        .corr(df["us10y_return"])
    )

    # ---------- Moving Average Distance ----------

    df["dxy_ma20"] = (
        df["dxy_close"]
        .rolling(20)
        .mean()
    )

    df["vix_ma20"] = (
        df["vix_close"]
        .rolling(20)
        .mean()
    )

    df["us10y_ma20"] = (
        df["us10y_close"]
        .rolling(20)
        .mean()
    )

    df["dxy_distance_20"] = (
        df["dxy_close"]
        /
        df["dxy_ma20"]
        - 1
    )

    df["vix_distance_20"] = (
        df["vix_close"]
        /
        df["vix_ma20"]
        - 1
    )

    df["us10y_distance_20"] = (
        df["us10y_close"]
        /
        df["us10y_ma20"]
        - 1
    )

    df.dropna(inplace=True)

    return df