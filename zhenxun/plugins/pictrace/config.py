from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zhenxun.configs.utils.models import RegisterConfig

MODULE_NAME = "pictrace"
LEGACY_MODULE_NAMES = ("pictracer", "pic_search")
DEFAULT_TAGGER_API_URL = "https://deepghs-wd14-tagging-online.hf.space/api/predict"
DEFAULT_TAGGER_MODEL = "wd14-vit"
SUPPORTED_TAGGER_MODELS = {"wd14-vit", "wd14-convnext"}


@dataclass(frozen=True, slots=True)
class PicSearchSettings:
    """搜图插件运行时配置快照。"""

    saucenao_api_key: str = ""
    serp_api_key: str = ""
    saucenao_low_threshold: float = 60
    saucenao_confident_threshold: float = 75
    saucenao_result_limit: int = 5
    google_lens_result_limit: int = 10
    google_lens_safe_search: bool = True
    google_lens_hide_thumbnail: bool = False
    tracemoe_result_limit: int = 5
    animetrace_result_limit: int = 5
    saucenao_nsfw_hide_level: int = 2
    hide_thumbnail: bool = False
    tracemoe_hide_adult_media: bool = True
    animetrace_show_media: bool = True
    anilist_cache_ttl_hours: int = 168
    max_image_size_mb: int = 15
    wait_image_timeout: int = 180
    forward_search_result: bool = True
    tagger_api_url: str = DEFAULT_TAGGER_API_URL
    tagger_model: str = DEFAULT_TAGGER_MODEL
    tagger_confidence_threshold: float = 0.5
    tagger_result_limit: int = 30
    tagger_request_timeout: int = 120
    tagger_hf_token: str = ""
    forward_tagger_result: bool = True


REGISTER_CONFIGS = [
    RegisterConfig(
        module=MODULE_NAME,
        key="SAUCENAO_API_KEY",
        value="",
        default_value="",
        help="SauceNAO API Key",
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="SERP_API_KEY",
        value="",
        default_value="",
        help="SerpAPI API Key，用于 Google Lens 搜索",
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="SAUCENAO_LOW_THRESHOLD",
        value=60,
        default_value=60,
        help="SauceNAO 低可信阈值，低于该值只展示 Google Lens",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="SAUCENAO_CONFIDENT_THRESHOLD",
        value=75,
        default_value=75,
        help="SauceNAO 高可信阈值，高于该值只展示 SauceNAO",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="SAUCENAO_RESULT_LIMIT",
        value=5,
        default_value=5,
        help="SauceNAO 返回结果数量",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="GOOGLE_LENS_RESULT_LIMIT",
        value=10,
        default_value=10,
        help="Google Lens 返回结果数量",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="GOOGLE_LENS_SAFE_SEARCH",
        value=True,
        default_value=True,
        help="Google Lens 是否启用安全搜索",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="GOOGLE_LENS_HIDE_THUMBNAIL",
        value=False,
        default_value=False,
        help="Google Lens 是否隐藏缩略图",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TRACEMOE_RESULT_LIMIT",
        value=5,
        default_value=5,
        help="搜番返回结果数量",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ANIMETRACE_RESULT_LIMIT",
        value=5,
        default_value=5,
        help="识角色返回结果数量",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="SAUCENAO_NSFW_HIDE_LEVEL",
        value=2,
        default_value=2,
        help="SauceNAO hide 参数，0-3",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="HIDE_THUMBNAIL",
        value=False,
        default_value=False,
        help="是否隐藏所有缩略图",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TRACEMOE_HIDE_ADULT_MEDIA",
        value=True,
        default_value=True,
        help="TraceMoe 命中成人内容时隐藏预览媒体",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ANIMETRACE_SHOW_MEDIA",
        value=True,
        default_value=True,
        help="AnimeTrace 是否显示角色裁剪预览",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="ANILIST_CACHE_TTL_HOURS",
        value=168,
        default_value=168,
        help="AniList 补全信息内存缓存时长",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="MAX_IMAGE_SIZE_MB",
        value=15,
        default_value=15,
        help="允许搜索的最大图片大小 MB",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="WAIT_IMAGE_TIMEOUT",
        value=180,
        default_value=180,
        help="命令未带图时等待用户发送图片的秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="FORWARD_SEARCH_RESULT",
        value=True,
        default_value=True,
        help="是否优先使用合并转发发送结果",
        type=bool,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TAGGER_API_URL",
        value=DEFAULT_TAGGER_API_URL,
        default_value=DEFAULT_TAGGER_API_URL,
        help="图片 tag 识别 API 地址，默认使用 Hugging Face 公共 WD14 Space",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TAGGER_MODEL",
        value=DEFAULT_TAGGER_MODEL,
        default_value=DEFAULT_TAGGER_MODEL,
        help="图片 tag 识别模型，可选 wd14-vit / wd14-convnext",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TAGGER_CONFIDENCE_THRESHOLD",
        value=0.5,
        default_value=0.5,
        help="图片 tag 识别置信度阈值",
        type=float,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TAGGER_RESULT_LIMIT",
        value=30,
        default_value=30,
        help="图片 tag 识别最多展示的 tag 数量",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TAGGER_REQUEST_TIMEOUT",
        value=120,
        default_value=120,
        help="请求图片 tag 识别 API 的超时时间秒数",
        type=int,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="TAGGER_HF_TOKEN",
        value="",
        default_value="",
        help="可选 Hugging Face Token，用于提升公共 Space 调用限额稳定性",
        type=str,
    ),
    RegisterConfig(
        module=MODULE_NAME,
        key="FORWARD_TAGGER_RESULT",
        value=True,
        default_value=True,
        help="是否优先使用合并转发发送图片 tag 识别结果",
        type=bool,
    ),
]


def _parse_bool(value) -> bool:
    """把配置层可能返回的 bool/数字/字符串统一解析为布尔值。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disable", "disabled"}:
            return False
    return bool(value)


def _clamp_float(
    value: Any, default: float, min_value: float, max_value: float
) -> float:
    """把外部配置收敛到接口接受的数值范围。"""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _positive_int(value: Any, default: int) -> int:
    """读取正整数配置，并在配置异常时回退默认值。"""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _normalize_tagger_model(value: Any) -> str:
    """把图片 tag 模型配置限制在公共 Space 当前支持的候选值内。"""

    model = str(value or DEFAULT_TAGGER_MODEL).strip()
    return model if model in SUPPORTED_TAGGER_MODELS else DEFAULT_TAGGER_MODEL


def load_settings() -> PicSearchSettings:
    """从项目配置层读取插件配置。"""

    # 延迟导入配置实例，避免插件入口被单独导入时提前触发项目配置层循环导入。
    from zhenxun.configs.config import Config

    def get_config(key: str, default):
        """读取 pictrace 配置，并兼容旧模块名配置。"""

        value = Config.get_config(MODULE_NAME, key, default)
        for legacy_module in LEGACY_MODULE_NAMES:
            legacy_value = Config.get_config(legacy_module, key, default)
            # 插件正式名迁移到 pictrace 后，测试期可能仍有旧模块名配置；
            # 新配置保持默认值时逐级回退，避免 API key 等配置在重命名后失效。
            if value == default and legacy_value != default:
                return legacy_value
        return value

    low_threshold = float(get_config("SAUCENAO_LOW_THRESHOLD", 60))
    confident_threshold = float(
        get_config("SAUCENAO_CONFIDENT_THRESHOLD", 75)
    )

    # 阈值顺序写反会导致分段不可预测，这里运行时纠正并保留最保守的低阈值含义。
    if confident_threshold < low_threshold:
        low_threshold, confident_threshold = confident_threshold, low_threshold

    tagger_api_url = (
        str(get_config("TAGGER_API_URL", DEFAULT_TAGGER_API_URL)).strip()
        or DEFAULT_TAGGER_API_URL
    )

    return PicSearchSettings(
        saucenao_api_key=str(get_config("SAUCENAO_API_KEY", "")),
        serp_api_key=str(get_config("SERP_API_KEY", "")),
        saucenao_low_threshold=low_threshold,
        saucenao_confident_threshold=confident_threshold,
        saucenao_result_limit=int(get_config("SAUCENAO_RESULT_LIMIT", 5)),
        google_lens_result_limit=int(get_config("GOOGLE_LENS_RESULT_LIMIT", 10)),
        google_lens_safe_search=_parse_bool(
            get_config("GOOGLE_LENS_SAFE_SEARCH", True)
        ),
        google_lens_hide_thumbnail=_parse_bool(
            get_config("GOOGLE_LENS_HIDE_THUMBNAIL", False)
        ),
        tracemoe_result_limit=int(get_config("TRACEMOE_RESULT_LIMIT", 5)),
        animetrace_result_limit=int(get_config("ANIMETRACE_RESULT_LIMIT", 5)),
        saucenao_nsfw_hide_level=int(get_config("SAUCENAO_NSFW_HIDE_LEVEL", 2)),
        hide_thumbnail=_parse_bool(get_config("HIDE_THUMBNAIL", False)),
        tracemoe_hide_adult_media=_parse_bool(
            get_config("TRACEMOE_HIDE_ADULT_MEDIA", True)
        ),
        animetrace_show_media=_parse_bool(get_config("ANIMETRACE_SHOW_MEDIA", True)),
        anilist_cache_ttl_hours=int(get_config("ANILIST_CACHE_TTL_HOURS", 168)),
        max_image_size_mb=int(get_config("MAX_IMAGE_SIZE_MB", 15)),
        wait_image_timeout=int(get_config("WAIT_IMAGE_TIMEOUT", 180)),
        forward_search_result=_parse_bool(get_config("FORWARD_SEARCH_RESULT", True)),
        tagger_api_url=tagger_api_url,
        tagger_model=_normalize_tagger_model(
            get_config("TAGGER_MODEL", DEFAULT_TAGGER_MODEL)
        ),
        tagger_confidence_threshold=_clamp_float(
            get_config("TAGGER_CONFIDENCE_THRESHOLD", 0.5), 0.5, 0.0, 1.0
        ),
        tagger_result_limit=_positive_int(get_config("TAGGER_RESULT_LIMIT", 30), 30),
        tagger_request_timeout=_positive_int(
            get_config("TAGGER_REQUEST_TIMEOUT", 120), 120
        ),
        tagger_hf_token=str(get_config("TAGGER_HF_TOKEN", "")).strip(),
        forward_tagger_result=_parse_bool(get_config("FORWARD_TAGGER_RESULT", True)),
    )
