import pandas as pd

from features.technical import (
    add_technical_features
)

gold = pd.read_csv(
    "data/gold.csv"
)

gold = add_technical_features(
    gold
)

gold.to_csv(
    "data/gold_features.csv",
    index=False
)

print(gold.head())
print(gold.dtypes)

print(gold.tail())