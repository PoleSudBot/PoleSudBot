import contextlib
import importlib.util
import platform
from pathlib import Path
import tomllib

import nonebot
from nonebot.plugin import get_loaded_plugins

PROJECT_ROOT = Path(__file__).resolve().parent

htmlrender_browser_channel = None
system = platform.system()

if system == "Windows":
    import winreg

    paths = {
        "chrome": r"SOFTWARE\Clients\StartMenuInternet\Google Chrome\DefaultIcon",
        "msedge": r"SOFTWARE\Clients\StartMenuInternet\Microsoft Edge\DefaultIcon",
    }
    for name, path in paths.items():
        with contextlib.suppress(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
            htmlrender_browser_channel = name
            break

elif system == "Darwin":  # macOS
    mac_paths = {
        "chrome": "/Applications/Google Chrome.app",
        "msedge": "/Applications/Microsoft Edge.app",
    }
    for name, path in mac_paths.items():
        if Path(path).exists():
            htmlrender_browser_channel = name
            break

if htmlrender_browser_channel:
    nonebot.logger.info(
        f"使用 {htmlrender_browser_channel} 作为 htmlrender 驱动启动..."
    )


def _discover_vendor_plugin_modules() -> list[str]:
    plugins_root = PROJECT_ROOT / "plugins"
    if not plugins_root.is_dir():
        return []

    modules: list[str] = []
    for plugin_dir in sorted(plugins_root.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith((".", "_")):
            continue

        module_candidates: list[str] = []
        pyproject_file = plugin_dir / "pyproject.toml"
        if pyproject_file.exists():
            with pyproject_file.open("rb") as f:
                pyproject_data = tomllib.load(f)
            nonebot_config = pyproject_data.get("tool", {}).get("nonebot", {})
            module_candidates.extend(
                name
                for name in nonebot_config.get("plugins", [])
                if isinstance(name, str) and name
            )

            if not module_candidates:
                package_name = (
                    pyproject_data.get("project", {}).get("name")
                    or pyproject_data.get("tool", {}).get("poetry", {}).get("name")
                )
                if isinstance(package_name, str) and package_name:
                    module_candidates.append(package_name.replace("-", "_"))

        if not module_candidates:
            package_dirs = sorted(
                path.name
                for path in plugin_dir.iterdir()
                if path.is_dir()
                and not path.name.startswith((".", "_"))
                and (path / "__init__.py").exists()
            )
            if len(package_dirs) == 1:
                module_candidates.append(package_dirs[0])

        for module_name in module_candidates:
            if module_name not in modules:
                modules.append(module_name)

    return modules


def _load_vendor_plugins() -> None:
    loaded_plugins = {plugin.name for plugin in get_loaded_plugins()}
    for module_name in _discover_vendor_plugin_modules():
        if module_name in loaded_plugins:
            continue
        if importlib.util.find_spec(module_name) is None:
            nonebot.logger.warning(
                f"第三方插件 {module_name} 不可导入，已跳过自动加载"
            )
            continue
        try:
            nonebot.load_plugin(module_name)
        except Exception as exc:
            nonebot.logger.error(f"加载第三方插件 {module_name} 失败: {exc}")
        else:
            loaded_plugins.add(module_name)


# from nonebot.adapters.discord import Adapter as DiscordAdapter
# from nonebot.adapters.dodo import Adapter as DoDoAdapter
# from nonebot.adapters.kaiheila import Adapter as KaiheilaAdapter
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

nonebot.init(htmlrender_browser_channel=htmlrender_browser_channel)


driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
# driver.register_adapter(KaiheilaAdapter)
# driver.register_adapter(DoDoAdapter)
# driver.register_adapter(DiscordAdapter)

from zhenxun.services.db_context import disconnect

# driver.on_startup(init)
driver.on_shutdown(disconnect)

# nonebot.load_builtin_plugins("echo")
nonebot.load_plugins("zhenxun/builtin_plugins")
nonebot.load_plugins("zhenxun/plugins")
_load_vendor_plugins()


if __name__ == "__main__":
    nonebot.run()
