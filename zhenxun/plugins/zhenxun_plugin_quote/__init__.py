from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from pathlib import Path
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from .command.manage_commands import quote_manage_cmd  # noqa: F401
from .command.query_commands import (  # noqa: F401
    quote_stats_cmd,
    record_pool,
)
from .command.upload_commands import (  # noqa: F401
    generate_quote_cmd,
    make_record_cmd,
    save_img_cmd,
)
from .config import ensure_quote_path
from zhenxun.services import renderer_service

ensure_quote_path()
driver = get_driver()

QUOTE_ASSETS_PATH = Path(__file__).parent / "templates"


@PriorityLifecycle.on_startup(priority=9)
async def _init_quote_services():
    """
    初始化语录插件服务。
    必须在 RendererService (priority=10) 之前注册模板命名空间。
    """
    try:
        renderer_service.register_template_namespace("@quote", QUOTE_ASSETS_PATH)
        logger.info("语录插件模板命名空间 '@quote' 注册成功。", "群聊语录")
    except Exception as e:
        logger.error(f"注册语录插件模板命名空间失败: {e}", "群聊语录", e=e)

    try:
        from .services.ocr_service import OCRService

        await OCRService.initialize_engine()
        logger.info("OCR服务初始化完成", "群聊语录")
    except Exception as e:
        logger.error(f"OCR服务初始化失败: {e}", "群聊语录", e=e)


@driver.on_shutdown
async def shutdown_services():
    """关闭"""
    try:
        from .services.ocr_service import OCRService

        OCRService.shutdown()
        logger.info("OCR服务已关闭", "群聊语录")
    except Exception as e:
        logger.error(f"OCR服务关闭失败: {e}", "群聊语录", e=e)


__plugin_meta__ = PluginMetadata(
    name="群聊语录",
    description="一款QQ群语录库——支持上传聊天截图为语录，随机投放语录，关键词搜索语录精准投放",
    usage=(
        "### 核心功能\n"
        "`语录 [关键词/@用户] [-n 数量]` - 随机发送语录，可按关键词或用户筛选，默认 1 张，最多 10 张\n"
        "示例：`语录`、`语录 南极`、`语录南极`、`语录 @南极`、`语录 -n 5 1969`、`语录 1969 五连`、`语录1969五连`、`语录五连`\n\n"
        "`上传语录 [图片] [tag/@用户 ...]` - 上传图片作为语录，也可回复图片后发送 `上传语录`\n"
        "示例：回复图片后发送 `上传语录 南极`、`上传语录 [图片] 南极 @南极`\n\n"
        "`上传` - 迁移提示命令，会提示改用 `上传语录`\n\n"
        "`记录语录 [tag/@用户 ...]` - 回复文本消息后生成语录图片并保存，`记录` 为兼容别名\n"
        "示例：回复消息后发送 `记录语录 南极`、`记录语录`、`记录 @南极 经典`\n\n"
        "`生成 [-s 主题ID] [-n 数量] [-o|--only]` - 预览语录图片但不入库\n"
        "`记录语录 [-s 主题ID] [-n 数量] [-o|--only]` - 记录时使用指定主题或连续消息，`记录` 为兼容别名\n"
        "`-o/--only` 与 `-n` 连用时，只查找并记录被回复用户的消息\n\n"
        "### 主题与预览\n"
        "`语录主题` - 查看所有可用主题，超级用户可在群聊或私聊使用\n"
        "`语录主题 主题名` - 切换全局默认主题，仅超级用户可用\n"
        "`quote theme` - `语录主题` 的英文写法\n\n"
        "### 统计功能\n"
        "`语录统计 [热门/高产上传/高产被录] [数量]` - 查看群内语录统计\n"
        "示例：`语录统计 热门`、`语录统计 高产上传 5`、`quote stats 热门 10`\n\n"
        "### 管理功能\n"
        "`删除` - 仅在回复 Bot 发出的语录图片时删除该语录，需为上传者或满足删除权限\n"
        "`删除语录` / `del` - 回复语录时优先删除被回复语录，否则删除本群上一条语录，需满足删除权限\n\n"
        "`tag` - 回复语录图片后查看手动 tag\n"
        "`tag all` / `alltag` - 回复语录图片后查看全部 tag\n"
        "`tag add` / `tag del` - 回复语录图片后添加或删除手动 tag\n"
        "`addtag` / `deltag` / `tagadd` / `tagdel` - `tag add/del` 的等价别名\n\n"
        "`语录管理 keyword 词1 ...` - 删除包含任一关键词的语录，仅超级用户可用\n"
        "`语录管理 clear --uploader @用户/QQ号` - 清空指定上传者的语录，仅超级用户可用\n"
        "`语录管理 clear --quoted @用户/QQ号` - 清空指定被记录用户的语录，仅超级用户可用\n"
        "`语录管理 cleanup` - 清理已退群用户的相关语录，仅超级用户可用\n\n"
        "### 说明\n"
        "`语录南极`、`记录语录aaa bbb`、`记录aaa bbb`、`上传语录xxx` 支持命令头与正文不加空格\n"
        "`语录五连` 会被当成数量请求；如果要查关键词“五连/三连”，请使用 `语录 -n 1 五连`\n"
        "`语录` 一次最多获取 10 张；1-5 张会合并成一条消息，6-10 张会使用合并转发\n"
        "`tag` 系列只在回复语录图时生效，非回复场景会静默让路\n"
        "`删除` 只在回复语录图时有效，不会接管普通聊天里的“删除”\n"
        "手动 tag 与 OCR/AI/记录文本生成的自动 tag 分层存储，但查询时会合并匹配"
    ),
    type="application",
    homepage="https://github.com/webjoin111/zhenxun_plugin_quote",
    supported_adapters={"~onebot.v11"},
    extra=PluginExtraData(
        author="webjoin111",
        version="v1.1.6",
        admin_level=0,
        configs=[
            RegisterConfig(
                module="quote",
                key="OCR_ENGINE",
                value="easyocr",
                help="OCR引擎选择，可选值: easyocr, paddleocr",
                default_value="easyocr",
            ),
            RegisterConfig(
                module="quote",
                key="OCR_USE_GPU",
                value=True,
                help="是否使用GPU加速OCR识别",
                default_value=True,
            ),
            RegisterConfig(
                module="quote",
                key="AI_ENABLED",
                value=True,
                help="是否启用AI识别功能（启用后会先尝试使用AI识别，失败则降级使用OCR）",
                default_value=True,
            ),
            RegisterConfig(
                module="quote",
                key="OCR_AI_MODEL",
                value="Gemini/gemini-2.5-flash-lite-preview-06-17",
                help="用于OCR的、支持视觉功能的AI模型全名 (格式: Provider/ModelName)",
                default_value="Gemini/gemini-2.5-flash-lite-preview-06-17",
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_PATH",
                value="",
                help="语录图片保存路径（留空则使用默认路径：DATA_PATH/quote/images）",
                default_value="",
            ),
            RegisterConfig(
                module="quote",
                key="THEME",
                value="qq-native",
                help="生成语录卡片时默认使用的主题/皮肤名称。",
                default_value="qq-native",
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_TEXT_ONLY_THEME",
                value="",
                help="仅用于纯文本（可包含@）的单条语录的主题。留空则默认使用 THEME。",
                default_value="",
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_ALLOW_SELF_RECORD",
                value=False,
                help="是否允许用户使用「记录」命令记录自己的消息。",
                default_value=False,
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_ALLOW_BOT_RECORD",
                value=False,
                help="是否允许记录Bot本身发送的消息。",
                default_value=False,
            ),
            RegisterConfig(
                module="quote",
                key="DELETE_ADMIN_LEVEL",
                value=5,
                help="设置使用「删除」命令所需的权限等级。默认值为5，允许群管理员使用。",
                default_value=5,
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_UPLOAD_PENDING_EMOJI_ID",
                value="10024",
                help="上传语录进入 OCR/AI 处理中时使用的贴表情 ID，留空则关闭该提示。",
                default_value="10024",
            ),
        ],
    ).dict(),
)
