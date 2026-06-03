from ingest.download_asset import download_asset
from config.asset import ASSETS

for asset_name, ticker in ASSETS.items():
    print(f"Downloading data for {asset_name} ({ticker})...")
    download_asset(ticker, asset_name)

