from pathlib import Path

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services import renderer_service
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

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
    usage="""
## 💬 群聊语录

一款QQ群语录库——支持上传聊天截图为语录，随机投放语录，关键词搜索语录精准投放。

### 📷 核心功能

- **语录 [*关键词*] [*@用户*]** - 随机发送一条语录，可提供关键词或@用户筛选
  示例：`语录`
  示例：`语录 白丝`
  示例：`语录 @小真寻`
- **上传 [图片]** - 上传图片作为语录。也可直接回复一张图片消息并发送上传
- **记录** - 将回复的文本内容生成一张语录图片并保存

### 🎨 主题与预览

- **生成** / **记录 [-s 主题ID] [-n 数量] [-o|--only]** - 在生成或记录语录时，使用指定的主题样式。生成命令仅预览
  `-o, --only`: 当与 `-n` 连用时，将只查找并记录被回复用户的消息，忽略其他人的发言。
- **语录主题** - 查看所有可用的语录卡片主题 (超级用户)
- **语录主题 [主题名]** - 切换全局默认的语录主题 (超级用户)

### 📊 统计功能

- **语录统计 [热门/高产上传/高产被录] [*数量*]** - 显示群内语录统计信息
  示例：`语录统计 热门`
  示例：`语录统计 高产上传 5`

### 🛠️ 管理功能

- **删除** / **del** - 删除被回复的语录 (需回复 Bot 发送的语录图片，仅限带权限的管理)
- **语录管理 keyword [词...]** - 删除包含任一关键词的语录 (超级用户)
- **语录管理 clear --uploader/--quoted [@用户/QQ号]** - 清空指定用户上传或被记录的所有语录 (超级用户)
- **语录管理 cleanup** - 清理已退群用户的相关语录 (超级用户)
    """,
    type="application",
    homepage="https://github.com/webjoin111/zhenxun_plugin_quote",
    supported_adapters={"~onebot.v11"},
    extra=PluginExtraData(
        author="webjoin111",
        version="v1.1.5",
        admin_level=0,
        menu_type="功能",
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
        ],
    ).dict(),
)
