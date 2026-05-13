import importlib
import sys

_asset_cache_module = importlib.import_module(
    "zhenxun.services.sekai_resource.asset_cache"
)

AssetCacheProvider = _asset_cache_module.AssetCacheProvider
asset_cache_provider = _asset_cache_module.asset_cache_provider
get_settings = _asset_cache_module.get_settings

sys.modules[__name__] = _asset_cache_module

__all__ = ["AssetCacheProvider", "asset_cache_provider", "get_settings"]
