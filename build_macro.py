import pandas as pd

from features.macro import (
    merge_macro_assets,
    add_macro_features
)


def build_gold_macro():

    gold = pd.read_csv(
        "data/gold_features.csv"
    )

    dxy = pd.read_csv(
        "data/dxy.csv"
    )

    vix = pd.read_csv(
        "data/vix.csv"
    )

    us10y = pd.read_csv(
        "data/us10y.csv"
    )

    dataset = merge_macro_assets(
        gold,
        dxy,
        vix,
        us10y
    )

    dataset = add_macro_features(
        dataset
    )

    dataset.to_csv(
        "data/gold_macro.csv",
        index=False
    )

    print()

    print("Gold Macro Dataset")
    print(dataset.shape)

    print()
    print(dataset.columns)


if __name__ == "__main__":
    build_gold_macro()