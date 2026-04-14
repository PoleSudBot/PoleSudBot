import random

from zhenxun.configs.config import Config


def random_event(impression: float) -> int:
    """签到随机事件

    参数:
        impression: 好感度

    返回:
        额外金币
    """
    # 过渡期先停掉签到道具掉落，只保留额外金币，让旧库存自然消耗。
    gold = random.randint(
        1, random.randint(1, int(1 if impression < 1 else impression))
    )
    max_sign_gold = Config.get_config("sign_in", "MAX_SIGN_GOLD")
    gold = max_sign_gold if gold > max_sign_gold else gold
    return gold
