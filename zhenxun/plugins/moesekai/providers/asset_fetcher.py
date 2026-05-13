import importlib
import sys

_asset_fetcher_module = importlib.import_module(
    "zhenxun.services.sekai_resource.asset_fetcher"
)

AssetFetcher = _asset_fetcher_module.AssetFetcher
AsyncHttpx = _asset_fetcher_module.AsyncHttpx
asset_fetcher = _asset_fetcher_module.asset_fetcher

sys.modules[__name__] = _asset_fetcher_module

__all__ = ["AssetFetcher", "AsyncHttpx", "asset_fetcher"]
