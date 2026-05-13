import importlib
import sys

_assets_module = importlib.import_module("zhenxun.services.sekai_resource.assets")

AssetProvider = _assets_module.AssetProvider
AssetSourceName = _assets_module.AssetSourceName
asset_provider = _assets_module.asset_provider
get_settings = _assets_module.get_settings

sys.modules[__name__] = _assets_module

__all__ = ["AssetProvider", "AssetSourceName", "asset_provider", "get_settings"]
