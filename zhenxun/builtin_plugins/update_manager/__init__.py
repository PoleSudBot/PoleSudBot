from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any, Generic, TypeVar

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
import nonebot
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
from pydantic import BaseModel, Field

from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.services.update_manager import (
    AddPluginRequest,
    UpdateManagerService,
    job_store,
    register_plugin_configs,
    save_runtime_settings,
)
from zhenxun.services.update_manager.session import SESSION_COOKIE, UpdateSessionStore
from zhenxun.utils.enum import PluginType
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

RT = TypeVar("RT")
session_store = UpdateSessionStore()

register_plugin_configs()

__plugin_meta__ = PluginMetadata(
    name="更新管理",
    description="管理 Bot 本体、资源仓库与第三方插件 fork 的更新",
    usage="提供 WebUI 更新管理页面与 API。",
    extra=PluginExtraData(
        author="k1yuyu",
        version="0.1.2",
        plugin_type=PluginType.HIDDEN,
        configs=[
            RegisterConfig(
                module="update-manager",
                key="AUTO_ENABLED",
                value=False,
                help="是否启用自动更新",
                type=bool,
                default_value=False,
            ),
            RegisterConfig(
                module="update-manager",
                key="AUTO_TIME",
                value="04:30",
                help="自动更新执行时间，格式 HH:MM",
                type=str,
                default_value="04:30",
            ),
        ],
    ).to_dict(),
)

driver = nonebot.get_driver()
router = APIRouter(prefix="/zhenxun/api/update-manager")
page_router = APIRouter()
service = UpdateManagerService()


class ApiResult(BaseModel, Generic[RT]):
    """更新管理 API 返回模型，保持旧 WebUI Result 字段但避免导入其包。"""

    suc: bool
    code: int = 200
    info: str = "操作成功"
    data: RT | None = None

    @classmethod
    def ok(cls, data: Any = None, info: str = "操作成功") -> "ApiResult[RT]":
        return cls(suc=True, info=info, data=data)

    @classmethod
    def fail(cls, info: str, code: int = 500) -> "ApiResult[RT]":
        return cls(suc=False, info=info, code=code)


def _client_ip(request: Request) -> str:
    """解析客户端 IP，用于 Bot 进程内免登录会话绑定。"""
    return session_store.client_ip(
        request.headers,
        request.client.host if request.client else None,
    )


def _is_authenticated(request: Request) -> bool:
    """校验当前 IP 与浏览器 cookie 是否匹配内存会话。"""
    return session_store.valid(_client_ip(request), request.cookies.get(SESSION_COOKIE))


def authentication():
    """校验更新管理自己的内存会话，避免依赖 WebUI 登录态。"""

    def inner(request: Request) -> None:
        if not _is_authenticated(request):
            raise HTTPException(
                status_code=401,
                detail="更新管理登录已失效，请重新登录。",
            )

    return Depends(inner)


class LoginRequest(BaseModel):
    """更新管理登录请求。"""

    password: str


class UpdateRequest(BaseModel):
    """手动更新请求范围。"""

    repo_ids: list[str] | None = None
    kinds: list[str] | None = None
    force: bool = False


class CheckoutRequest(BaseModel):
    """切换到指定 commit 的请求。"""

    repo_id: str
    commit_hash: str
    force: bool = False


class UpstreamCheckRequest(BaseModel):
    """上游检测请求范围。"""

    repo_ids: list[str] | None = None


class SettingsRequest(BaseModel):
    """允许 WebUI 修改的更新管理开关。"""

    values: dict[str, Any] = Field(default_factory=dict)


def _managed_repo_ids() -> set[str]:
    """读取当前可管理仓库 id，用于 API 入参提前失败。"""
    return {repo.id for repo in service.list_managed_repos()}


def _validate_repo_ids(repo_ids: list[str] | None) -> None:
    """校验仓库 id，避免后台任务静默处理空目标。"""
    if not repo_ids:
        return
    unknown = sorted(set(repo_ids) - _managed_repo_ids())
    if unknown:
        raise HTTPException(status_code=404, detail=f"未知仓库：{', '.join(unknown)}")


def _validate_kinds(kinds: list[str] | None) -> None:
    """校验仓库类型过滤条件，避免无效类型被静默忽略。"""
    if not kinds:
        return
    allowed = {"root", "resources", "plugin"}
    unknown = sorted(set(kinds) - allowed)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知仓库类型：{', '.join(unknown)}",
        )


@router.get("/session")
async def get_session(request: Request):
    """检查当前 IP 与 cookie 是否已有有效更新管理会话。"""
    return ApiResult.ok({"authenticated": _is_authenticated(request)})


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    """使用 WebUI 密码登录更新管理，成功后写入进程内免登录会话。"""
    configured = str(Config.get_config("web-ui", "password", "") or "")
    if not configured or not secrets.compare_digest(payload.password, configured):
        raise HTTPException(status_code=401, detail="密码错误。")

    client_ip = _client_ip(request)
    if not client_ip:
        raise HTTPException(status_code=400, detail="无法识别客户端 IP。")

    session = session_store.create(client_ip)
    response.set_cookie(
        SESSION_COOKIE,
        session,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return ApiResult.ok({"authenticated": True})


@router.post("/logout", dependencies=[authentication()])
async def logout(request: Request, response: Response):
    """清理当前 IP 的更新管理登录会话。"""
    session_store.clear(_client_ip(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return ApiResult.ok({"authenticated": False})


@router.get("/settings", dependencies=[authentication()])
async def get_settings():
    """读取当前更新管理配置。"""
    return ApiResult.ok(service.refresh_settings())


@router.put("/settings", dependencies=[authentication()])
async def put_settings(payload: SettingsRequest):
    """保存更新管理配置并刷新服务内缓存。"""
    try:
        settings = save_runtime_settings(payload.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    service.refresh_settings()
    _refresh_auto_update_job()
    return ApiResult.ok(settings)


@router.get("/repositories", dependencies=[authentication()])
async def get_repositories(fetch: bool = False):
    """列出被管理仓库，fetch=true 时刷新 origin 状态。"""
    if fetch:
        repos = await job_store.run_exclusive(
            lambda: service.scan_repositories(fetch=True)
        )
    else:
        repos = await service.scan_repositories(fetch=False)
    return ApiResult.ok(repos)


@router.get("/repositories/{repo_id}/commits", dependencies=[authentication()])
async def get_repository_commits(repo_id: str, fetch: bool = True, limit: int = 30):
    """按需读取目标分支 commit 历史。"""
    _validate_repo_ids([repo_id])
    if fetch:
        commits = await job_store.run_exclusive(
            lambda: service.list_commits(repo_id, fetch=True, limit=limit)
        )
    else:
        commits = await service.list_commits(repo_id, fetch=False, limit=limit)
    return ApiResult.ok(commits)


@router.post("/repositories/update", dependencies=[authentication()])
async def update_repositories(
    payload: UpdateRequest = Body(default_factory=UpdateRequest),
):
    """创建手动 origin ff-only 更新任务。"""
    _validate_repo_ids(payload.repo_ids)
    _validate_kinds(payload.kinds)
    job = job_store.start(
        kind="update",
        title="手动更新仓库",
        write=True,
        coro_factory=lambda record: service.update_repositories(
            repo_ids=payload.repo_ids,
            kinds=payload.kinds,  # type: ignore[arg-type]
            force=payload.force,
        ),
    )
    return ApiResult.ok(job)


@router.post("/repositories/checkout", dependencies=[authentication()])
async def checkout_repository(payload: CheckoutRequest):
    """创建切换到指定 commit 的写任务。"""
    _validate_repo_ids([payload.repo_id])
    job = job_store.start(
        kind="checkout",
        title=f"切换仓库 {payload.repo_id} 到 {payload.commit_hash[:7]}",
        write=True,
        coro_factory=lambda record: service.checkout_commit(
            repo_id=payload.repo_id,
            commit_hash=payload.commit_hash,
            force=payload.force,
        ),
    )
    return ApiResult.ok(job)


@router.post("/repositories/upstream-check", dependencies=[authentication()])
async def upstream_check(
    payload: UpstreamCheckRequest = Body(default_factory=UpstreamCheckRequest),
):
    """创建第三方插件 upstream behind 只读检测任务。"""
    _validate_repo_ids(payload.repo_ids)
    job = job_store.start(
        kind="upstream_check",
        title="检测插件上游更新",
        write=True,
        coro_factory=lambda record: service.check_upstream(repo_ids=payload.repo_ids),
    )
    return ApiResult.ok(job)


@router.post("/plugins/add", dependencies=[authentication()])
async def add_plugin(payload: AddPluginRequest):
    """创建新增第三方插件任务。"""
    job = job_store.start(
        kind="add_plugin",
        title=f"新增插件 {payload.repo_url}",
        write=True,
        coro_factory=lambda record: service.add_plugin(payload),
    )
    return ApiResult.ok(job)


@router.get("/jobs", dependencies=[authentication()])
async def recent_jobs(limit: int = 20):
    """返回最近任务摘要。"""
    return ApiResult.ok(job_store.recent(limit))


@router.get("/jobs/{job_id}", dependencies=[authentication()])
async def get_job(job_id: str):
    """查询单个任务状态。"""
    job = job_store.get(job_id)
    if not job:
        return ApiResult.fail("任务不存在", code=404)
    return ApiResult.ok(job)


@page_router.get("/update")
async def update_manager_page():
    """提供轻量独立更新管理页面，不改已有 WebUI 构建产物。"""
    html = (Path(__file__).parent / "page.html").read_text("utf-8")
    return HTMLResponse(html)


@page_router.get("/zhenxun/update-manager", include_in_schema=False)
async def old_update_manager_page():
    """兼容旧入口，避免已有书签直接失效。"""
    return RedirectResponse("/update", status_code=307)


async def _auto_update_task() -> None:
    """执行自动更新；默认关闭且只做 origin ff-only。"""
    settings = service.refresh_settings()
    if not settings.auto_enabled:
        return
    job = await job_store.run_now(
        kind="update",
        title="自动更新仓库",
        write=True,
        coro_factory=lambda record: service.update_repositories(
            kinds=settings.auto_scope
        ),
    )
    if job.state == "failed":
        summary = f"自动更新失败：{job.error or '未知错误'}"
        logger.warning(summary, "更新管理")
        await _send_auto_update_report([summary])
        return
    results = job.result if isinstance(job.result, list) else []
    updated = [item for item in results if item.status == "updated"]
    failed = [item for item in results if item.status in {"failed", "skipped"}]
    if not updated and not failed:
        logger.info("自动更新完成：没有发现新版本。", "更新管理")
        return
    summary = f"自动更新完成：更新 {len(updated)} 个，失败/阻断 {len(failed)} 个。"
    logger.info(summary, "更新管理")
    await _send_auto_update_report(_build_auto_update_report_nodes(results))


def _short_hash(hash_value: str | None) -> str:
    """生成报告里使用的短 commit id，缺失时保留占位。"""
    return hash_value[:7] if hash_value else "-"


def _forward_sender_id() -> str:
    """为合并转发节点选择发送者 id；无可用 Bot 时使用占位避免报告构造失败。"""
    for bot in nonebot.get_bots().values():
        return str(bot.self_id)
    return "0"


def _auto_update_status_title(result: Any) -> str:
    """把结构化更新状态转换为超级用户报告里的短状态。"""
    if result.status == "updated":
        return "更新成功"
    if result.status == "failed":
        return "更新失败"
    if result.status == "skipped":
        return "更新阻断"
    return "未更新"


def _build_auto_update_report_nodes(results: list[Any]) -> list[str]:
    """按仓库拆分自动更新报告；没有变化的仓库不占用转发节点。"""
    nodes: list[str] = []
    for result in results:
        if result.status == "unchanged":
            continue
        lines = [
            f"{result.repo.name}｜{_auto_update_status_title(result)}",
            f"类型：{result.repo.kind}",
            f"分支：{result.repo.target_branch}",
            f"版本：{_short_hash(result.old_head)} -> {_short_hash(result.new_head)}",
            f"结果：{result.message}",
        ]
        if result.needs_restart:
            lines.append("提示：需要重启后生效")
        if result.needs_uv_sync:
            lines.append("提示：依赖文件有变化，建议执行 uv sync")
        if result.status == "updated":
            if result.commits:
                lines.append("提交：")
                lines.extend(
                    f"- {commit.short_hash} {commit.subject}"
                    for commit in result.commits
                )
                if result.omitted_commit_count:
                    lines.append(f"- 还有 {result.omitted_commit_count} 个提交未展示")
            else:
                lines.append("提交：未读取到本次更新的 commit 明细")
        nodes.append("\n".join(lines))
    return nodes


async def _send_auto_update_report(nodes: list[str]) -> None:
    """发送自动更新合并转发报告；发送失败只记录日志，不影响更新结果。"""
    if not nodes:
        return
    name = BotConfig.self_nickname or "更新管理"
    message = MessageUtils.alc_forward_msg(nodes, _forward_sender_id(), name)
    try:
        await PlatformUtils.send_superuser(None, message)
    except Exception as exc:
        logger.warning("更新管理自动更新通知失败", "更新管理", e=exc)


def _refresh_auto_update_job() -> None:
    """根据当前配置添加或移除自动更新定时任务。"""
    settings = service.refresh_settings()
    job_id = "update_manager_auto_update"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not settings.auto_enabled:
        return
    hour_text, minute_text = settings.auto_time.split(":", 1)
    scheduler.add_job(
        _auto_update_task,
        "cron",
        hour=int(hour_text),
        minute=int(minute_text),
        id=job_id,
        replace_existing=True,
    )


@PriorityLifecycle.on_startup(priority=1)
async def _startup() -> None:
    try:
        app: FastAPI = nonebot.get_app()
        app.include_router(router)
        app.include_router(page_router)
        _refresh_auto_update_job()
        await service.capture_running_heads(overwrite=True)
        logger.info("更新管理 API 启动成功", "更新管理")
    except Exception as exc:
        logger.error("更新管理 API 启动失败", "更新管理", e=exc)
