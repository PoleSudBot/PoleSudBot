import re

from zhenxun.services.log import logger

from .utils import get_user_dynamics


async def is_ad(uid: int, dynamic_id: str) -> bool:
    """使用 Bilibili API 检查动态内容是否为广告"""
    try:
        logger.info(f"[广告过滤-API] 开始检查动态: UID={uid}, 动态ID={dynamic_id}")

        logger.debug(f"[广告过滤-API] 正在获取用户动态数据: UID={uid}")
        dynamics_data = await get_user_dynamics(uid)
        if not dynamics_data or not dynamics_data.get("items"):
            logger.warning(
                f"[广告过滤-API] 未获取到动态数据: UID={uid}, 数据为空或无items字段"
            )
            return False

        items = dynamics_data["items"]
        logger.debug(
            f"[广告过滤-API] 成功获取动态数据: UID={uid}, 动态数量={len(items)}"
        )

        logger.debug(f"[广告过滤-API] 正在查找指定动态: UID={uid}, 动态ID={dynamic_id}")
        target_dynamic = None
        available_ids = []
        for item in items:
            item_id = str(item.get("id_str", ""))
            available_ids.append(item_id)
            if item_id == str(dynamic_id):
                target_dynamic = item
                break

        if not target_dynamic:
            logger.warning(
                f"[广告过滤-API] 未找到指定动态: "
                f"UID={uid}, 动态ID={dynamic_id}, "
                f"可用动态ID={available_ids[:5]}..."
            )
            return False

        logger.debug(f"[广告过滤-API] 成功找到目标动态: UID={uid}, 动态ID={dynamic_id}")

        # 新版 API 使用 item.type 表示动态类型
        dynamic_type = target_dynamic.get("type", "")
        logger.debug(
            f"[广告过滤-API] 动态类型检查: "
            f"UID={uid}, 动态ID={dynamic_id}, "
            f"类型={dynamic_type}"
        )

        # 新版 API 中的商品类型
        goods_types = {
            "DYNAMIC_TYPE_ARTICLE": "专栏文章",
            "DYNAMIC_TYPE_COMMON_SQUARE": "商品分享",
        }

        if dynamic_type in goods_types:
            logger.warning(
                f"[广告过滤-API] 检测到商品类型动态: "
                f"UID={uid}, 动态ID={dynamic_id}, "
                f"类型={dynamic_type}"
                f"({goods_types[dynamic_type]})"
            )
            return True

        logger.debug(
            f"[广告过滤-API] 动态类型检查通过: "
            f"UID={uid}, 动态ID={dynamic_id}, "
            f"类型={dynamic_type}"
        )

        # 从新版 API 的 modules 中提取文本内容
        logger.debug(f"[广告过滤-API] 开始检查动态内容: UID={uid}, 动态ID={dynamic_id}")
        text_content = ""
        content_sources = []

        modules = target_dynamic.get("modules", {})
        module_dynamic = modules.get("module_dynamic", {})

        # 从 desc 中提取文字
        desc = module_dynamic.get("desc", {})
        if desc and desc.get("text"):
            text_content += desc["text"]
            content_sources.append("desc.text")

        # 从 major 中提取文字
        major = module_dynamic.get("major", {})
        if major:
            # opus 类型
            if major.get("opus"):
                summary = major["opus"].get("summary", {})
                if summary and summary.get("text"):
                    text_content += summary["text"]
                    content_sources.append("major.opus.summary")
            # article 类型
            if major.get("article"):
                title = major["article"].get("title", "")
                desc_text = major["article"].get("desc", "")
                if title:
                    text_content += title
                    content_sources.append("major.article.title")
                if desc_text:
                    text_content += desc_text
                    content_sources.append("major.article.desc")

        logger.debug(
            f"[广告过滤-API] 提取文本内容: "
            f"UID={uid}, 动态ID={dynamic_id}, "
            f"来源={content_sources}, "
            f"长度={len(text_content)}"
        )

        logger.debug(f"[广告过滤-API] 开始关键词检查: UID={uid}, 动态ID={dynamic_id}")
        ad_keywords = [
            "商品",
            "购买",
            "链接",
            "店铺",
            "优惠",
            "折扣",
            "带货",
            "种草",
            "好物",
            "推荐",
            "下单",
            "抢购",
            "限时",
            "特价",
            "促销",
            "¥",
            "￥",
            "元",
            "价格",
            "原价",
            "现价",
            "到手价",
            "淘宝",
            "天猫",
            "京东",
            "拼多多",
            "抖音",
            "小红书",
            "直播间",
            "橱窗",
            "购物车",
            "加购",
            "收藏",
        ]

        text_lower = text_content.lower()
        found_keywords = []
        for keyword in ad_keywords:
            if keyword in text_content or keyword.lower() in text_lower:
                found_keywords.append(keyword)

        if found_keywords:
            logger.warning(
                f"[广告过滤-API] 检测到广告关键词: UID={uid}, 动态ID={dynamic_id}, 关键词={found_keywords}"
            )
            return True

        logger.debug(f"[广告过滤-API] 关键词检查通过: UID={uid}, 动态ID={dynamic_id}")

        logger.debug(f"[广告过滤-API] 开始商品卡片检查: UID={uid}, 动态ID={dynamic_id}")
        goods_fields = []
        if major and major.get("goods"):
            goods_fields.append("goods")
        if major and major.get("common"):
            goods_fields.append("common")

        if goods_fields:
            logger.warning(
                f"[广告过滤-API] 检测到商品卡片: "
                f"UID={uid}, 动态ID={dynamic_id}, "
                f"字段={goods_fields}"
            )
            return True

        logger.debug(f"[广告过滤-API] 商品卡片检查通过: UID={uid}, 动态ID={dynamic_id}")

        logger.debug(f"[广告过滤-API] 开始商品链接检查: UID={uid}, 动态ID={dynamic_id}")
        url_patterns = {
            r"item\.taobao\.com": "淘宝商品",
            r"detail\.tmall\.com": "天猫商品",
            r"item\.jd\.com": "京东商品",
            r"yangkeduo\.com": "拼多多商品",
            r"haohuo\.jinritemai\.com": "抖音好货",
        }

        for pattern, platform in url_patterns.items():
            if re.search(pattern, text_content, re.IGNORECASE):
                logger.warning(
                    f"[广告过滤-API] 检测到商品链接: UID={uid}, 动态ID={dynamic_id}, 平台={platform}, 模式={pattern}"
                )
                return True

        logger.debug(f"[广告过滤-API] 商品链接检查通过: UID={uid}, 动态ID={dynamic_id}")
        logger.info(
            f"[广告过滤-API] 动态内容检查完成，未发现广告: UID={uid}, 动态ID={dynamic_id}"
        )
        return False

    except Exception as e:
        logger.error(
            f"[广告过滤-API] API方式检查动态内容失败: UID={uid}, 动态ID={dynamic_id}, 错误类型={type(e).__name__}, 错误={e}"
        )
        import traceback

        logger.debug(f"[广告过滤-API] 详细错误信息:\n{traceback.format_exc()}")
        return False
