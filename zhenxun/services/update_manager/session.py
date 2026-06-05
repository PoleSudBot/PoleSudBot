from __future__ import annotations

from collections.abc import Mapping
import secrets
import time

SESSION_COOKIE = "update_manager_session"
SESSION_TTL = 86400  # 24 小时


class UpdateSessionStore:
    """保存 Bot 进程内的更新管理免登录会话。"""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, float]] = {}

    def client_ip(self, headers: Mapping[str, str], client_host: str | None) -> str:
        """解析客户端 IP，优先兼容常见反向代理转发头。"""
        forwarded_for = headers.get("x-forwarded-for", "")
        forwarded_ip = forwarded_for.split(",", 1)[0].strip()
        if forwarded_ip:
            return forwarded_ip
        return client_host or ""

    def create(self, client_ip: str) -> str:
        """为当前 IP 创建新的随机会话。"""
        session = secrets.token_urlsafe(32)
        self._sessions[client_ip] = (session, time.time())
        return session

    def valid(self, client_ip: str, session: str | None) -> bool:
        """校验当前 IP 与 cookie 会话是否匹配，过期会话自动清理。"""
        entry = self._sessions.get(client_ip)
        if entry is None:
            return False
        expected, created_at = entry
        if time.time() - created_at > SESSION_TTL:
            self._sessions.pop(client_ip, None)
            return False
        return bool(
            client_ip
            and session
            and secrets.compare_digest(session, expected)
        )

    def clear(self, client_ip: str) -> None:
        """清理当前 IP 的会话。"""
        self._sessions.pop(client_ip, None)

    def clear_all(self) -> None:
        """测试或进程重置时清空全部会话。"""
        self._sessions.clear()
