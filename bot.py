# bot.py (修复增强版)
import importlib
import importlib.util
import os
from pathlib import Path
import pkgutil
import sys

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
from nonebot.log import logger
from nonebot.plugin import get_loaded_plugins

# --- NoneBot 初始化 ---
nonebot.init()
app = nonebot.get_asgi()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)


# ========== 手动配置区 ==========
PRIORITY_PLUGINS: list[str] = [
    # 如需优先加载的插件（按顺序），写模块名或包名的猜测：
    # "nonebot_plugin_apscheduler",
]

MANUAL_USER_PLUGINS: list[str] = [
    # 手动标记为用户插件（可写猜测名，脚本会尝试解析）：
    "nonebot_plugin_acgalaxy",
    "yetanotherpicsearch",
]

env_manual = os.getenv("NB_MANUAL_PLUGINS", "")
if env_manual:
    for p in [x.strip() for x in env_manual.split(",") if x.strip()]:
        MANUAL_USER_PLUGINS.append(p)

env_priority = os.getenv("NB_PLUGIN_PRIORITY", "")
if env_priority:
    for p in [x.strip() for x in env_priority.split(",") if x.strip()]:
        PRIORITY_PLUGINS.append(p)
# ==============================


def _normalize_name_for_fs(name: str) -> str:
    return name.replace("-", "_").lower()


def _to_module_path_candidate(raw: str) -> str:
    raw = raw.strip()
    if "." in raw:
        return raw
    if raw.startswith("nonebot_plugin_"):
        return raw
    return f"plugins.{raw}"


def resolve_module_name(candidate: str, available_modules: list[str]) -> str | None:
    """
    尝试将用户提供的 candidate（可能含 'plugins.' 前缀或分发名）解析为实际可 import 的模块名。
    返回可直接传给 nonebot.load_plugin 的模块名，或 None（解析失败）。
    """
    # 1) 如果 candidate 可直接 import，直接返回
    try:
        if importlib.util.find_spec(candidate) is not None:
            return candidate
    except Exception:
        pass

    # 2) 尝试一些常见的变形（去前缀/加前缀/替换 - -> _ / 只取最后一段）
    name = candidate.split(".")[-1]
    variants = [
        name,
        name.replace("-", "_"),
        f"nonebot_plugin_{name}",
        f"nonebot_plugin_{name.replace('-', '_')}",
        f"plugins.{name}",
        f"plugins.{name.replace('-', '_')}",
        f"zhenxun.{name}",
        f"zhenxun.{name.replace('-', '_')}",
        f"zhenxun_plugin_{name}",
        f"zhenxun_plugin_{name.replace('-', '_')}",
    ]

    for v in variants:
        try:
            if importlib.util.find_spec(v) is not None:
                return v
        except Exception:
            continue

    # 3) 在系统已安装模块列表中做归一化匹配（大小写 / '-' -> '_'）
    norm_target = name.lower().replace("-", "_")
    for m in available_modules:
        if m.lower().replace("-", "_") == norm_target:
            return m

    # 4) 再做更宽松的包含匹配（避免太多误判）
    for m in available_modules:
        nm = m.lower().replace("-", "_")
        if norm_target in nm or nm in norm_target:
            return m

    return None


def load_plugins_and_report():
    PROJECT_ROOT = Path(__file__).resolve().parent
    BUILTIN_PLUGINS_ROOT = PROJECT_ROOT / "zhenxun" / "builtin_plugins"
    ZHENXUN_PLUGINS_ROOT = PROJECT_ROOT / "zhenxun" / "plugins"
    USER_PLUGINS_ROOT = PROJECT_ROOT / "plugins"

    plugin_classification = {}

    # 发现内建与真寻（文件夹）
    if BUILTIN_PLUGINS_ROOT.is_dir():
        for item in BUILTIN_PLUGINS_ROOT.iterdir():
            if item.is_dir() and not item.name.startswith((".", "_")):
                module_name = f"zhenxun.builtin_plugins.{item.name}"
                plugin_classification[module_name] = "内建"

    if ZHENXUN_PLUGINS_ROOT.is_dir():
        for item in ZHENXUN_PLUGINS_ROOT.iterdir():
            if item.is_dir() and not item.name.startswith((".", "_")):
                module_name = f"zhenxun.plugins.{item.name}"
                plugin_classification[module_name] = "真寻"

    # 本地 plugins/ 下的目录集合
    user_plugin_names: set[str] = set()
    try:
        if USER_PLUGINS_ROOT.is_dir():
            for item in USER_PLUGINS_ROOT.iterdir():
                if item.is_dir() and not item.name.startswith((".", "_")):
                    user_plugin_names.add(_normalize_name_for_fs(item.name))
    except Exception:
        pass

    # 手动用户插件标记
    manual_candidates = []
    for raw in MANUAL_USER_PLUGINS:
        cand = _to_module_path_candidate(raw)
        plugin_classification[cand] = "用户"
        manual_candidates.append(cand)

    # 从已安装模块发现 nonebot 插件包
    available_modules = [m.name for m in pkgutil.iter_modules()]

    for _, name, _ in pkgutil.iter_modules():
        if name.startswith("nonebot_plugin_") or name.startswith("_nb_plugin"):
            if name in plugin_classification:
                continue
            if name.lower() in user_plugin_names:
                plugin_classification[name] = "用户"
            else:
                plugin_classification[name] = "依赖"

    # 统计结构
    stats = {
        "内建": {"succeeded": 0, "failed": 0},
        "真寻": {"succeeded": 0, "failed": 0},
        "用户": {"succeeded": 0, "failed": 0},
        "依赖": {"succeeded": 0, "failed": 0},
        "skipped": 0,
    }

    loaded_plugin_names = {p.name for p in get_loaded_plugins()}

    logger.info(
        f"--- 发现 {len(plugin_classification)} 个待加载插件，开始执行静默加载计划... ---"
    )

    failed_list = []
    succeeded_list = []
    skipped_list = []

    # 构造加载顺序：priority -> manual -> rest
    final_order = []
    for raw in PRIORITY_PLUGINS:
        cand = _to_module_path_candidate(raw)
        if cand not in final_order:
            final_order.append(cand)
            if cand not in plugin_classification:
                plugin_classification[cand] = "依赖"

    for cand in manual_candidates:
        if cand not in final_order:
            final_order.append(cand)

    for cand in sorted(plugin_classification.keys()):
        if cand not in final_order:
            final_order.append(cand)

    for module_path in final_order:
        category = plugin_classification.get(module_path, "依赖")
        plugin_name_base = module_path.split(".")[-1]

        if (
            module_path in loaded_plugin_names
            or plugin_name_base in loaded_plugin_names
        ):
            stats["skipped"] += 1
            skipped_list.append(module_path)
            continue

        # 先解析实际可 import 的模块名
        resolved = resolve_module_name(module_path, available_modules)
        to_load = resolved or module_path

        try:
            nonebot.load_plugin(to_load)
            stats[category]["succeeded"] += 1
            succeeded_list.append(to_load)
            loaded_plugin_names.update(p.name for p in get_loaded_plugins())
            if resolved and resolved != module_path:
                logger.info(f"✅ 解析并加载: '{module_path}' -> 实际模块 '{resolved}'")
        except ModuleNotFoundError as mnfe:
            # 若尝试过解析但仍未找到，记录并继续
            logger.opt(exception=mnfe).error(
                f"❌ 无法找到模块 '{to_load}'（原始: {module_path}），跳过。"
            )
            stats[category]["failed"] += 1
            failed_list.append((module_path, repr(mnfe)))
        except RuntimeError as re_ex:
            txt = str(re_ex)
            # 如果是 "not loaded as a plugin" 这类错误，尝试从 sys.modules 清理并重试一次（谨慎）
            if "not loaded as a plugin" in txt.lower():
                if to_load in sys.modules:
                    logger.warning(
                        f"⚠️ 模块 '{to_load}' 已被普通 import（非插件方式）导入。尝试从 sys.modules 删除并重试加载（有副作用）。"
                    )
                    try:
                        del sys.modules[to_load]
                    except Exception:
                        logger.exception("从 sys.modules 删除失败，跳过重试。")
                    else:
                        try:
                            nonebot.load_plugin(to_load)
                            stats[category]["succeeded"] += 1
                            succeeded_list.append(to_load)
                            loaded_plugin_names.update(
                                p.name for p in get_loaded_plugins()
                            )
                            logger.info(f"✅ 通过清理 sys.modules 成功加载 '{to_load}'")
                            continue
                        except Exception as e2:
                            logger.opt(exception=e2).error(
                                f"❌ 重试加载 '{to_load}' 失败（清理后）。"
                            )
                            stats[category]["failed"] += 1
                            failed_list.append((module_path, repr(e2)))
                            continue
            # 其它 runtime 错误记录
            logger.opt(exception=re_ex).error(
                f"❌ 插件 {to_load} 加载失败（RuntimeError）。"
            )
            stats[category]["failed"] += 1
            failed_list.append((module_path, repr(re_ex)))
        except Exception as e:
            logger.opt(exception=e).error(f"❌ 插件 {to_load} 加载失败（未知异常）。")
            stats[category]["failed"] += 1
            failed_list.append((module_path, repr(e)))

    # 输出表格报告（保留原风格）
    BOX_CHAR = {
        "top_left": "╔",
        "top_right": "╗",
        "bottom_left": "╚",
        "bottom_right": "╝",
        "horizontal": "═",
        "vertical": "║",
        "mid_left": "╠",
        "mid_right": "╣",
    }
    HEADER = "🔌 插件加载审计报告 🔌"
    WIDTH = 66

    logger.info(" ")
    logger.info(
        f"{BOX_CHAR['top_left']}{BOX_CHAR['horizontal'] * (WIDTH - 2)}{BOX_CHAR['top_right']}"
    )
    logger.info(
        f"{BOX_CHAR['vertical']} {HEADER.center(WIDTH - 4)} {BOX_CHAR['vertical']}"
    )
    logger.info(
        f"{BOX_CHAR['mid_left']}{BOX_CHAR['horizontal'] * (WIDTH - 2)}{BOX_CHAR['mid_right']}"
    )

    total_succeeded = sum(
        stats[c]["succeeded"] for c in ("内建", "真寻", "用户", "依赖")
    )
    total_failed = sum(stats[c]["failed"] for c in ("内建", "真寻", "用户", "依赖"))

    for category in ("内建", "真寻", "用户", "依赖"):
        s_count = stats[category]["succeeded"]
        f_count = stats[category]["failed"]
        line = f"  {category:<8} {s_count:>3} 个成功 | {f_count:>3} 个失败"
        logger.info(f"{BOX_CHAR['vertical']}{line:<{WIDTH - 2}}{BOX_CHAR['vertical']}")

    logger.info(
        f"{BOX_CHAR['mid_left']}{BOX_CHAR['horizontal'] * (WIDTH - 2)}{BOX_CHAR['mid_right']}"
    )
    skipped_line = f"  ⏭️ 已跳过 : {stats['skipped']} 个 (已加载或被依赖提前加载)"
    logger.info(
        f"{BOX_CHAR['vertical']}{skipped_line:<{WIDTH - 2}}{BOX_CHAR['vertical']}"
    )

    summary_line = f"  ✅ 总计: {total_succeeded} 个成功, {total_failed} 个失败"
    if total_failed == 0:
        logger.info(
            f"{BOX_CHAR['vertical']}{summary_line:<{WIDTH - 2}}{BOX_CHAR['vertical']}"
        )
    else:
        logger.warning(
            f"{BOX_CHAR['vertical']}{summary_line:<{WIDTH - 2}}{BOX_CHAR['vertical']}"
        )

    logger.info(
        f"{BOX_CHAR['bottom_left']}{BOX_CHAR['horizontal'] * (WIDTH - 2)}{BOX_CHAR['bottom_right']}"
    )
    logger.info(" ")

    if succeeded_list:
        logger.info("--- ✅ 加载成功（清单）---")
        for name in succeeded_list:
            logger.info(f"  • {name}")

    if failed_list:
        logger.info("--- ❌ 加载失败（示例）---")
        for name, err in failed_list:
            logger.info(f"  • {name} -> {err}")

    if skipped_list:
        logger.info("--- ⏭️ 跳过的插件 ---")
        for name in skipped_list:
            logger.info(f"  • {name}")

    logger.info(" ")
    user_plugin_list = sorted(
        [n for n, c in plugin_classification.items() if c == "用户"]
    )
    dependency_plugin_list = sorted(
        [n for n, c in plugin_classification.items() if c == "依赖"]
    )

    if user_plugin_list:
        logger.info("--- 👤 用户插件列表 (User Plugins) ---")
        for name in user_plugin_list:
            logger.info(f"  • {name}")
    if dependency_plugin_list:
        logger.info("--- 🔩 依赖插件列表 (Dependency Plugins) ---")
        for name in dependency_plugin_list:
            logger.info(f"  • {name}")
    logger.info(" ")


if __name__ == "__main__":
    load_plugins_and_report()
    nonebot.run(app="__mp_main__:app")
