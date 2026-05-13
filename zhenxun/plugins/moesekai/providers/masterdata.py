import importlib
import sys

_master_data_module = importlib.import_module(
    "zhenxun.services.sekai_resource.master_data"
)

MasterDataProvider = _master_data_module.MasterDataProvider
RegionUpdateResult = _master_data_module.RegionUpdateResult
SourceRevisionInfo = _master_data_module.SourceRevisionInfo
master_data_provider = _master_data_module.master_data_provider
get_settings = _master_data_module.get_settings

sys.modules[__name__] = _master_data_module

__all__ = [
    "MasterDataProvider",
    "RegionUpdateResult",
    "SourceRevisionInfo",
    "get_settings",
    "master_data_provider",
]
