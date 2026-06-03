from ingest.download_asset import download_asset
from config.asset import ASSETS


def main():

    for asset_name, ticker in ASSETS.items():

        print(
            f"Downloading {asset_name} ({ticker})..."
        )

        download_asset(
            ticker=ticker,
            filename=asset_name
        )

    print("Download Complete")


if __name__ == "__main__":
    main()