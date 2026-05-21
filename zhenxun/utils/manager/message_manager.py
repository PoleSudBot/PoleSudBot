import time
from typing import ClassVar


class MessageManager:
    data: ClassVar[dict[str, list[str]]] = {}
    triggered_data: ClassVar[dict[tuple[str, str], list[str]]] = {}
    triggered_reply_index: ClassVar[dict[tuple[str, str], str]] = {}
    triggered_order: ClassVar[list[tuple[str, str]]] = []
    recalled_trigger_sources: ClassVar[dict[tuple[str, str], float]] = {}
    recalled_trigger_order: ClassVar[list[tuple[str, str]]] = []
    _max_triggered_source_messages = 1000
    _max_recalled_trigger_sources = 1000

    @classmethod
    def add(cls, uid: str, msg_id: str):
        """记录用户触发过的机器人消息。"""
        if uid not in cls.data:
            cls.data[uid] = []
        cls.data[uid].append(msg_id)
        cls.remove_check(uid)

    @classmethod
    def check(cls, uid: str, msg_id: str) -> bool:
        """检查指定机器人消息是否由该用户触发。"""
        return msg_id in cls.data.get(uid, [])

    @classmethod
    def remove_check(cls, uid: str):
        """限制单个用户可手动撤回的历史消息数量。"""
        if len(cls.data[uid]) > 200:
            cls.data[uid] = cls.data[uid][100:]

    @classmethod
    def get(cls, uid: str) -> list[str]:
        """获取用户触发过的机器人消息列表。"""
        return cls.data[uid] if uid in cls.data else []

    @classmethod
    def add_triggered(
        cls, bot_self_id: str | int, source_msg_id: str | int, bot_msg_id: str | int
    ):
        """记录一条用户消息触发出的机器人回复消息。"""
        bot_self_id = str(bot_self_id)
        source_msg_id = str(source_msg_id)
        bot_msg_id = str(bot_msg_id)
        key = (bot_self_id, source_msg_id)
        reply_key = (bot_self_id, bot_msg_id)
        previous_source_msg_id = cls.triggered_reply_index.get(reply_key)
        if previous_source_msg_id and previous_source_msg_id != source_msg_id:
            # message_id 理论上唯一；重绑前先摘掉旧来源，避免正反索引分叉。
            cls._remove_triggered_reply_from_source(
                bot_self_id, previous_source_msg_id, bot_msg_id
            )
        if key not in cls.triggered_data:
            cls.triggered_data[key] = []
            cls.triggered_order.append(key)
        if bot_msg_id not in cls.triggered_data[key]:
            cls.triggered_data[key].append(bot_msg_id)
        cls.triggered_reply_index[reply_key] = source_msg_id
        cls.remove_triggered_check()

    @classmethod
    def pop_triggered(
        cls, bot_self_id: str | int, source_msg_id: str | int
    ) -> list[str]:
        """取出并清空一条用户消息触发出的机器人回复消息。"""
        key = (str(bot_self_id), str(source_msg_id))
        if key in cls.triggered_order:
            cls.triggered_order.remove(key)
        message_ids = cls.triggered_data.pop(key, [])
        for message_id in message_ids:
            cls.triggered_reply_index.pop((str(bot_self_id), message_id), None)
        return message_ids

    @classmethod
    def remove_triggered_reply(
        cls, bot_self_id: str | int, bot_msg_id: str | int
    ) -> bool:
        """从自动撤回关联中移除一条已被撤回的机器人回复。"""
        bot_self_id = str(bot_self_id)
        bot_msg_id = str(bot_msg_id)
        source_msg_id = cls.triggered_reply_index.pop((bot_self_id, bot_msg_id), None)
        if source_msg_id is None:
            return False
        return cls._remove_triggered_reply_from_source(
            bot_self_id, source_msg_id, bot_msg_id
        )

    @classmethod
    def mark_trigger_source_recalled(
        cls, bot_self_id: str | int, source_msg_id: str | int
    ) -> list[str]:
        """标记触发消息已撤回，并取出当前已关联的机器人回复。"""
        key = (str(bot_self_id), str(source_msg_id))
        if key not in cls.recalled_trigger_sources:
            cls.recalled_trigger_order.append(key)
        cls.recalled_trigger_sources[key] = time.monotonic()
        cls.remove_recalled_trigger_check()
        return cls.pop_triggered(bot_self_id, source_msg_id)

    @classmethod
    def is_trigger_source_recalled(
        cls, bot_self_id: str | int, source_msg_id: str | int
    ) -> bool:
        """检查触发消息是否已经被用户撤回。"""
        cls.remove_recalled_trigger_check()
        return (str(bot_self_id), str(source_msg_id)) in cls.recalled_trigger_sources

    @classmethod
    def remove_triggered_check(cls):
        """限制自动撤回关联缓存的源消息数量，避免长期运行时内存无限增长。"""
        if len(cls.triggered_order) <= cls._max_triggered_source_messages:
            return
        expired_count = len(cls.triggered_order) - cls._max_triggered_source_messages
        expired_keys = cls.triggered_order[:expired_count]
        cls.triggered_order = cls.triggered_order[expired_count:]
        for key in expired_keys:
            for message_id in cls.triggered_data.pop(key, []):
                cls.triggered_reply_index.pop((key[0], message_id), None)

    @classmethod
    def _remove_triggered_reply_from_source(
        cls, bot_self_id: str, source_msg_id: str, bot_msg_id: str
    ) -> bool:
        """从指定来源列表中移除一条机器人回复，并在列表为空时清理来源键。"""
        key = (bot_self_id, source_msg_id)
        message_ids = cls.triggered_data.get(key)
        if not message_ids:
            return False
        try:
            message_ids.remove(bot_msg_id)
        except ValueError:
            return False
        if not message_ids:
            cls.triggered_data.pop(key, None)
            if key in cls.triggered_order:
                cls.triggered_order.remove(key)
        return True

    @classmethod
    def remove_recalled_trigger_check(cls):
        """限制已撤回源消息缓存数量，避免晚到回复保护状态无限增长。"""
        # 2 分钟只是 QQ 侧用户撤回源消息的窗口；
        # 源消息已撤回后，晚到回复仍要靠 tombstone 立即清理。
        while cls.recalled_trigger_order:
            key = cls.recalled_trigger_order[0]
            if key not in cls.recalled_trigger_sources:
                cls.recalled_trigger_order.pop(0)
                continue
            break
        if len(cls.recalled_trigger_order) <= cls._max_recalled_trigger_sources:
            return
        expired_count = (
            len(cls.recalled_trigger_order) - cls._max_recalled_trigger_sources
        )
        expired_keys = cls.recalled_trigger_order[:expired_count]
        cls.recalled_trigger_order = cls.recalled_trigger_order[expired_count:]
        for key in expired_keys:
            cls.recalled_trigger_sources.pop(key, None)
