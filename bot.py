from pathlib import Path
import pkgutil

import nonebot
from nonebot.log import logger
from nonebot.plugin import get_loaded_plugins

# --- 核心初始化 ---
nonebot.init()
app = nonebot.get_asgi()
from nonebot.adapters.onebot.v11 import Adapter

driver = nonebot.get_driver()
driver.register_adapter(Adapter)


def load_all_plugins_explicitly():
    """
    一个显式、有序、带分类的终极插件加载器。
    - 发现所有插件，构建一个有序的加载计划。
    - 逐一加载，实时跟踪状态，根除重复加载问题。
    - 提供精准的分类审计报告。
    """
    # --- 1. 发现阶段：构建加载计划 ---
    PROJECT_ROOT = Path(__file__).resolve().parent
    BUILTIN_PLUGINS_ROOT = PROJECT_ROOT / "zhenxun" / "builtin_plugins"
    USER_PLUGINS_ROOT = PROJECT_ROOT / "zhenxun" / "plugins"

    # 用户插件白名单，用于分类
    user_plugin_names = {
        item.name.replace("-", "_")
        for item in USER_PLUGINS_ROOT.iterdir()
        if item.is_dir() and not item.name.startswith((".", "_"))
    }

    # 构建有序的加载计划列表，格式为 (module_path, category)
    plugins_to_load = []

    # 计划A: 内建插件
    if BUILTIN_PLUGINS_ROOT.is_dir():
        for subpath in BUILTIN_PLUGINS_ROOT.iterdir():
            if subpath.is_dir() and not subpath.name.startswith((".", "_")):
                # 使用点分隔路径作为模块路径
                module_path = f"zhenxun.builtin_plugins.{subpath.name}"
                plugins_to_load.append((module_path, "builtin"))

    # 计划B: 已安装的第三方插件
    for _, name, _ in pkgutil.iter_modules():
        if name.startswith("nonebot_plugin_") or name.startswith("_nb_plugin"):
            category = "user" if name in user_plugin_names else "dependency"
            plugins_to_load.append((name, category))

    # --- 2. 执行阶段：逐一加载 ---
    logger.info(
        f"--- 发现 {len(plugins_to_load)} 个待加载插件，开始执行加载计划... ---"
    )
    stats = {
        "builtin": {"succeeded": 0},
        "user": {"succeeded": 0, "failed": 0},
        "dependency": {"succeeded": 0, "failed": 0},
        "skipped": 0,
    }
    loaded_plugin_names = {p.name for p in get_loaded_plugins()}

    for module_path, category in plugins_to_load:
        # 实时检查插件是否已被依赖链提前加载
        # 注意: NoneBot 对插件名的处理可能和模块路径不完全一致，我们检查最核心的部分
        plugin_name_base = module_path.split(".")[-1]
        if (
            module_path in loaded_plugin_names
            or plugin_name_base in loaded_plugin_names
        ):
            logger.debug(f"插件 {module_path} 已被提前加载，跳过。")
            stats["skipped"] += 1
            continue

        log_prefix_map = {
            "builtin": "🏠 内建",
            "user": "👤 用户",
            "dependency": "🔩 依赖",
        }
        log_prefix = log_prefix_map[category]

        try:
            nonebot.load_plugin(module_path)
            logger.success(f"✅ {log_prefix}插件 {module_path} 加载成功。")
            stats[category]["succeeded"] += 1
            # 关键：实时更新我们自己的状态，包括新加载的插件本身及其可能引入的依赖
            loaded_plugin_names.update(p.name for p in get_loaded_plugins())
        except Exception as e:
            logger.opt(exception=e).error(
                f"❌ {log_prefix}插件 {module_path} 加载失败。"
            )
            if category != "builtin":
                stats[category]["failed"] += 1

    # --- 3. 报告阶段 ---
    logger.info("--- 插件加载审计报告 ---")
    # ... (报告部分与上一版相同，此处省略以保持简洁，你可以直接复用)
    b_s = stats["builtin"]["succeeded"]
    u_s, u_f = stats["user"]["succeeded"], stats["user"]["failed"]
    d_s, d_f = stats["dependency"]["succeeded"], stats["dependency"]["failed"]
    skipped = stats["skipped"]
    total_succeeded = b_s + u_s + d_s
    total_failed = u_f + d_f
    logger.info(f"内建插件: {b_s} 个成功加载")
    logger.info(f"用户插件: {u_s} 个成功, {u_f} 个失败")
    logger.info(f"依赖插件: {d_s} 个成功, {d_f} 个失败")
    summary_message = f"总计: {total_succeeded} 个成功, {total_failed} 个失败, {skipped} 个跳过(已提前加载)。"
    if total_failed > 0:
        logger.warning(summary_message)
    else:
        logger.success(summary_message)


if __name__ == "__main__":
    load_all_plugins_explicitly()
    nonebot.run(app="__mp_main__:app")
