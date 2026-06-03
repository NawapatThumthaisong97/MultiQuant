from pathlib import Path
import pandas as pd

from features.technical import (
    add_technical_features
)

DATA_DIR = Path("data")


def build_features():

    csv_files = DATA_DIR.glob("*.csv")

    for file in csv_files:

        if "_features" in file.name:
            continue

        if "_macro" in file.name:
            continue

        print(
            f"Building Features: {file.name}"
        )

        df = pd.read_csv(file)

        df = add_technical_features(df)

        output_file = (
            DATA_DIR /
            f"{file.stem}_features.csv"
        )

        df.to_csv(
            output_file,
            index=False
        )

        print(
            f"Saved: {output_file.name}"
        )


if __name__ == "__main__":
    build_features()