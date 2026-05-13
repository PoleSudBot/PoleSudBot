import importlib
import sys

_master_data_module = importlib.import_module(
    "zhenxun.services.sekai_resource.master_data"
)

MasterDataService = _master_data_module.MasterDataService
RegionUpdateResult = _master_data_module.RegionUpdateResult
SourceVersionInfo = _master_data_module.SourceVersionInfo
master_data_service = _master_data_module.master_data_service
get_settings = _master_data_module.get_settings

sys.modules[__name__] = _master_data_module

__all__ = [
    "MasterDataService",
    "RegionUpdateResult",
    "SourceVersionInfo",
    "get_settings",
    "master_data_service",
]
