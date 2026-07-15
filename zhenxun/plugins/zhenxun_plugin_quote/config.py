from pathlib import Path

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

QUOTE_ASSETS_PATH = Path(__file__).parent / "templates"


def get_quote_path() -> Path:
    """获取语录图片保存路径，优先使用配置，否则使用默认路径"""
    custom_path = Config.get_config("quote", "QUOTE_PATH", "")

    if custom_path:
        custom_path = normalize_path(custom_path)
        logger.info(f"使用自定义语录路径: {custom_path}", "群聊语录")
        return custom_path
    else:
        default_path = DATA_PATH / "quote" / "images"
        logger.debug(f"使用默认语录路径: {default_path}", "群聊语录")
        return default_path


def ensure_quote_path() -> Path:
    """确保语录图片目录存在并返回路径"""
    quote_path = get_quote_path()
    quote_path.mkdir(parents=True, exist_ok=True)
    return quote_path


def get_quote_group_path(group_id: str) -> Path:
    """为群组创建独立的语录图片目录。"""
    normalized_group_id = str(group_id).strip()
    if not normalized_group_id or any(
        marker in normalized_group_id for marker in ("/", "\\", "..")
    ):
        raise ValueError(f"非法群组 ID: {group_id}")
    group_path = ensure_quote_path() / normalized_group_id
    group_path.mkdir(parents=True, exist_ok=True)
    return group_path


def get_quote_image_path(filename: str) -> Path:
    """获取语录图片的完整路径"""
    quote_path = ensure_quote_path()
    return quote_path / filename


def normalize_path(path: str | Path) -> Path:
    """标准化路径，确保跨平台兼容性"""
    if isinstance(path, str):
        return Path(path)
    return path


def resolve_quote_image_path(path_str: str | Path) -> Path:
    """
    解析语录图片路径，无论是相对还是绝对，都返回一个可用的绝对路径。
    这是处理新旧两种路径格式的核心。
    """
    raw_path = Path(str(path_str).replace("\\", "/"))
    candidate = (
        raw_path.resolve()
        if raw_path.is_absolute()
        else (DATA_PATH / raw_path).resolve()
    )
    managed_roots = {DATA_PATH.resolve(), get_quote_path().resolve()}

    # 历史外置路径可能以相对 DATA_PATH 的 ../ 保存，但解析后必须仍落在当前受管目录内。
    if not any(candidate.is_relative_to(root) for root in managed_roots):
        raise ValueError(f"语录图片路径越界: {path_str}")
    return candidate


def safe_file_exists(file_path: str | Path) -> bool:
    """安全地检查文件是否存在，处理新旧两种路径格式。"""
    try:
        absolute_path = resolve_quote_image_path(file_path)
        return absolute_path.exists() and absolute_path.is_file()
    except (OSError, PermissionError, ValueError) as e:
        logger.warning(f"检查文件存在性时出错: {file_path}, 错误: {e}", "群聊语录")
        return False


def ensure_directory_exists(dir_path: str | Path) -> Path:
    """确保目录存在，如果不存在则创建"""
    try:
        path = normalize_path(dir_path)
        path.mkdir(parents=True, exist_ok=True)
        return path
    except (OSError, PermissionError) as e:
        logger.error(f"创建目录失败: {dir_path}, 错误: {e}", "群聊语录")
        raise
