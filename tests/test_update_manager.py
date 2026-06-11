import asyncio
import json
from pathlib import Path

import nonebot
import pytest

nonebot.init()

from zhenxun.services.update_manager.config import (
    _manager_table,
    load_settings,
    save_runtime_settings,
)
from zhenxun.services.update_manager.git import (
    AsyncCommandRunner,
    CommandError,
    CommandResult,
)
from zhenxun.services.update_manager.jobs import JobStore
from zhenxun.services.update_manager.manager import UpdateManagerService
from zhenxun.services.update_manager.models import (
    AddPluginRequest,
    ManagerSettings,
)
from zhenxun.services.update_manager.plugin_ops import (
    add_plugin,
    update_plugins_txt,
    update_pyproject_dependency,
)
from zhenxun.services.update_manager.repository import (
    build_managed_repos,
    get_repo_status,
    list_repo_commits,
    parse_github_repo,
    read_editable_plugin_sources,
    read_plugin_urls,
    resolve_remote_default_branch,
)
from zhenxun.services.update_manager.session import UpdateSessionStore


class FakeRunner:
    def __init__(self):
        self.commands: list[tuple[Path, tuple[str, ...]]] = []
        self.branch = "dev"
        self.head = "local-head"
        self.origin_head = "future-head"
        self.tracked_dirty = ""
        self.behind = "0 2"
        self.fail_fetch = False
        self.origin_branch_exists = False
        self.gh_repo_exists = False

    async def git(self, cwd: Path, *args: str, timeout=None, check: bool = True):
        self.commands.append((cwd, args))
        if args[:2] == ("clone", "https://github.com/source/demo-plugin.git"):
            target = Path(args[2])
            (target / ".git").mkdir(parents=True)
            (target / "pyproject.toml").write_text(
                '[project]\nname = "demo-plugin"\n',
                "utf-8",
            )
            return CommandResult(("git", *args), 0)
        if args[:3] == ("remote", "rename", "origin"):
            return CommandResult(("git", *args), 0)
        if args[:3] == ("remote", "get-url", "origin"):
            return CommandResult(("git", *args), 0, "https://github.com/PoleSudBot/repo.git")
        if args[:3] == ("remote", "get-url", "upstream"):
            return CommandResult(("git", *args), 0, "https://github.com/source/repo.git")
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return CommandResult(("git", *args), 0, self.branch)
        if args == ("rev-parse", "HEAD"):
            return CommandResult(("git", *args), 0, self.head)
        if args[:3] == ("rev-parse", "--verify", "origin/dev"):
            return CommandResult(("git", *args), 0, self.origin_head)
        if args[:3] == ("rev-list", "--left-right", "--count"):
            return CommandResult(("git", *args), 0, self.behind)
        if args[:2] == ("rev-list", "--count"):
            return CommandResult(("git", *args), 0, "2")
        if args == ("status", "--porcelain"):
            return CommandResult(("git", *args), 0, self.tracked_dirty)
        if args == ("ls-files", "--others", "--exclude-standard"):
            return CommandResult(("git", *args), 0, "scratch.txt")
        if args[:3] == ("ls-remote", "--heads", "origin"):
            stdout = "abc123\trefs/heads/L7dev" if self.origin_branch_exists else ""
            return CommandResult(("git", *args), 0, stdout)
        if args[:3] == ("symbolic-ref", "refs/remotes/upstream/HEAD"):
            return CommandResult(("git", *args), 0, "refs/remotes/upstream/main")
        if args[:2] == ("fetch", "origin") and self.fail_fetch:
            result = CommandResult(("git", *args), 128, "", "network down")
            if check:
                raise CommandError(result)
            return result
        if args[:2] in {("fetch", "origin"), ("fetch", "upstream")}:
            return CommandResult(("git", *args), 0)
        if args[:2] == ("reset", "--hard"):
            self.head = args[2]
            return CommandResult(("git", *args), 0)
        if args[:3] == ("pull", "--ff-only", "origin"):
            self.head = self.origin_head
            return CommandResult(("git", *args), 0)
        if args[:2] == ("merge", "--ff-only"):
            self.head = args[2]
            return CommandResult(("git", *args), 0)
        if args[:2] == ("merge-base", "--is-ancestor"):
            returncode = 1 if args[2] == "bad-hash" else 0
            return CommandResult(("git", *args), returncode)
        if args[:2] == ("log", "--format=%H%x00%s"):
            return CommandResult(
                ("git", *args),
                0,
                "future-head\x00new feature\nmiddle-head\x00middle work\n",
            )
        if args[:1] == ("log",):
            return CommandResult(
                ("git", *args),
                0,
                "future-head\x00Alice\x002026-05-27\x00new feature\n"
                "local-head\x00Bob\x002026-05-26\x00current work\n",
            )
        if args[:2] == ("remote", "add"):
            return CommandResult(("git", *args), 0)
        if args[:2] in {("checkout", "-b"), ("checkout", "-B")}:
            return CommandResult(("git", *args), 0)
        if args[:2] == ("branch", "--set-upstream-to"):
            return CommandResult(("git", *args), 0)
        if args[:2] == ("push", "-u"):
            return CommandResult(("git", *args), 0)
        if check:
            raise AssertionError(f"unexpected git command: {args}")
        return CommandResult(("git", *args), 1, "", "missing")

    async def gh(self, cwd: Path, *args: str, timeout=None, check: bool = True):
        self.commands.append((cwd, args))
        if args[:2] == ("repo", "view"):
            if self.gh_repo_exists:
                return CommandResult(("gh", *args), 0)
            return CommandResult(("gh", *args), 1, "", "not found")
        if args[:2] == ("repo", "create"):
            return CommandResult(("gh", *args), 0)
        if check:
            raise AssertionError(f"unexpected gh command: {args}")
        return CommandResult(("gh", *args), 1, "", "missing")


def test_parse_and_read_plugin_urls(tmp_path: Path):
    plugins = tmp_path / "plugins.txt"
    plugins.write_text(
        "# comment\nhttps://github.com/owner/demo.git\n\n# disabled\n",
        "utf-8",
    )

    assert parse_github_repo("https://github.com/owner/demo.git") == "owner/demo"
    assert read_plugin_urls(plugins) == ["https://github.com/owner/demo.git"]


def test_read_editable_plugin_sources_excludes_sidecar(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'nonebot-plugin-demo = {path = "plugins/demo", editable = true}\n'
        'escaped-plugin = {path = "plugins/../resources/evil", editable = true}\n'
        'nonebot-plugin-genshinuid = {path = "sidecar/.runtime/vendors/'
        'nonebot-plugin-genshinuid", editable = true}\n'
        'plain-package = {path = "packages/plain", editable = true}\n',
        "utf-8",
    )

    assert read_editable_plugin_sources(pyproject, "plugins") == [Path("plugins/demo")]


def test_build_managed_repos_uses_editable_paths_and_excludes_sidecar(tmp_path: Path):
    (tmp_path / "plugins.txt").write_text(
        "https://github.com/owner/demo\n",
        "utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv.sources]\n"
        'nonebot-plugin-demo = {path = "plugins/demo", editable = true}\n'
        'nonebot-plugin-genshinuid = {path = "sidecar/.runtime/vendors/'
        'nonebot-plugin-genshinuid", editable = true}\n',
        "utf-8",
    )
    settings = ManagerSettings(project_root=tmp_path)

    repos = build_managed_repos(settings)

    assert [repo.id for repo in repos] == ["root", "resources", "plugin:demo"]
    assert repos[-1].path == tmp_path / "plugins" / "demo"
    assert repos[-1].source_url == "https://github.com/owner/demo"


def test_update_plugins_txt_is_idempotent(tmp_path: Path):
    plugins = tmp_path / "plugins.txt"
    plugins.write_text("# group\nhttps://github.com/owner/a\n", "utf-8")

    assert not update_plugins_txt(plugins, "https://github.com/owner/a")
    assert update_plugins_txt(plugins, "https://github.com/owner/b")
    assert plugins.read_text("utf-8").splitlines()[-1] == "https://github.com/owner/b"


def test_update_pyproject_dependency_preserves_existing_tables(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    plugin = tmp_path / "plugins" / "demo"
    plugin.mkdir(parents=True)
    pyproject.write_text(
        '[project]\ndependencies = ["existing"]\n[tool.uv.sources]\n',
        "utf-8",
    )

    changed = update_pyproject_dependency(pyproject, "Demo_Plugin", plugin)

    text = pyproject.read_text("utf-8")
    assert changed
    assert '"demo-plugin"' in text
    assert 'demo-plugin = {path = "plugins/demo", editable = true}' in text


def test_update_session_store_binds_session_to_client_ip():
    store = UpdateSessionStore()
    client_ip = store.client_ip({}, "127.0.0.1")
    session = store.create(client_ip)

    assert store.valid("127.0.0.1", session)
    assert not store.valid("203.0.113.7", session)
    assert store.client_ip({"x-forwarded-for": "198.51.100.8, 10.0.0.1"}, None) == (
        "198.51.100.8"
    )

    store.clear(client_ip)
    assert not store.valid("127.0.0.1", session)


def test_save_runtime_settings_rejects_invalid_values_before_write(monkeypatch):
    calls = []

    def fake_set_config(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "zhenxun.services.update_manager.config.Config.set_config",
        fake_set_config,
    )

    with pytest.raises(ValueError, match="AUTO_TIME"):
        save_runtime_settings({"AUTO_TIME": "25:61"})
    with pytest.raises(ValueError, match="MAX_PARALLEL"):
        save_runtime_settings({"MAX_PARALLEL": "4"})
    with pytest.raises(ValueError, match="AUTO_SCOPE"):
        save_runtime_settings({"AUTO_SCOPE": ["root", "sidecar"]})
    with pytest.raises(ValueError, match="未知配置项"):
        save_runtime_settings({"UNKNOWN": True})

    assert calls == []


def test_load_settings_defaults_auto_update_disabled_at_0430(
    monkeypatch,
    tmp_path: Path,
):
    def fake_get_config(_module, _key, default=None):
        return default

    monkeypatch.setattr(
        "zhenxun.services.update_manager.config.Config.get_config",
        fake_get_config,
    )

    settings = load_settings(tmp_path)

    assert not settings.auto_enabled
    assert settings.auto_time == "04:30"


@pytest.mark.asyncio
async def test_job_store_run_now_and_exclusive_share_write_lock(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.json")
    order: list[str] = []
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def write_task(record):
        order.append("write-start")
        write_started.set()
        await release_write.wait()
        order.append("write-end")
        return "done"

    async def exclusive_scan():
        order.append("exclusive")
        return "scan"

    write_future = asyncio.create_task(
        store.run_now(
            kind="update",
            title="write",
            write=True,
            coro_factory=write_task,
        )
    )
    await write_started.wait()

    exclusive_future = asyncio.create_task(store.run_exclusive(exclusive_scan))
    await asyncio.sleep(0)

    assert order == ["write-start"]

    release_write.set()
    job = await write_future
    exclusive_result = await exclusive_future

    assert job.state == "success"
    assert exclusive_result == "scan"
    assert order == ["write-start", "write-end", "exclusive"]


@pytest.mark.asyncio
async def test_repo_status_blocks_tracked_dirty(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    runner.tracked_dirty = " M pyproject.toml"

    status = await get_repo_status(
        runner,
        settings_repo(repo_path),
        fetch=True,
    )

    assert status.tracked_dirty
    assert not status.can_update
    assert status.update_block_reason == "存在已跟踪文件的未提交修改"


@pytest.mark.asyncio
async def test_repo_status_reports_fetch_failure_without_raising(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    runner.fail_fetch = True

    status = await get_repo_status(
        runner,
        settings_repo(repo_path),
        fetch=True,
    )

    assert status.update_block_reason == "状态采集失败"
    assert status.error
    assert "network down" in status.error


@pytest.mark.asyncio
async def test_service_scan_repository_fetches_single_repo_status(tmp_path: Path):
    (tmp_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=runner)

    status = await service.scan_repository("root", fetch=True)

    assert status.repo.id == "root"
    assert status.behind == 2
    assert any(
        command[1] == ("fetch", "origin", "--prune")
        for command in runner.commands
    )


@pytest.mark.asyncio
async def test_force_update_ignores_tracked_dirty_without_clean(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    runner.tracked_dirty = " M pyproject.toml"
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=runner)

    result = await service.update_one(settings_repo(repo_path), force=True)

    assert result.status == "updated"
    assert result.message == "强制更新完成"
    assert [commit.short_hash for commit in result.commits] == ["future-", "middle-"]
    assert any(
        command[1] == ("reset", "--hard", "origin/dev")
        for command in runner.commands
    )
    assert not any(command[1][:1] == ("clean",) for command in runner.commands)


@pytest.mark.asyncio
async def test_normal_update_still_blocks_tracked_dirty(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    runner.tracked_dirty = " M pyproject.toml"
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=runner)

    result = await service.update_one(settings_repo(repo_path))

    assert result.status == "skipped"
    assert result.message == "存在已跟踪文件的未提交修改"
    assert not any(command[1][:2] == ("reset", "--hard") for command in runner.commands)


def test_service_validate_selection_rejects_unknown_targets(tmp_path: Path):
    (tmp_path / ".git").mkdir(parents=True)
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=FakeRunner())

    with pytest.raises(ValueError, match="未知仓库"):
        service.validate_selection(repo_ids=["plugin:missing"])
    with pytest.raises(ValueError, match="未知仓库类型"):
        service.validate_selection(kinds=["sidecar"])


@pytest.mark.asyncio
async def test_list_commits_parses_target_branch_history(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner()

    result = await list_repo_commits(runner, settings_repo(repo_path), fetch=True)

    assert result.current_hash == "local-head"
    assert result.latest_hash == "future-head"
    assert [commit.short_hash for commit in result.commits] == ["future-", "local-h"]
    assert result.commits[0].author == "Alice"
    assert result.commits[0].message == "new feature"


@pytest.mark.asyncio
async def test_service_commit_history_keeps_running_head_until_restart(tmp_path: Path):
    (tmp_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=runner)

    before = await service.list_commits("root", fetch=True)
    results = await service.update_repositories(repo_ids=["root"])
    after = await service.list_commits("root", fetch=False)

    assert before.current_hash == "local-head"
    assert before.running_hash == "local-head"
    assert after.current_hash == "future-head"
    assert after.running_hash == "local-head"
    assert after.latest_hash == "future-head"
    assert [commit.subject for commit in results[0].commits] == [
        "new feature",
        "middle work",
    ]


@pytest.mark.asyncio
async def test_checkout_rejects_commit_outside_target_branch(tmp_path: Path):
    (tmp_path / ".git").mkdir(parents=True)
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=FakeRunner())

    result = await service.checkout_commit(
        repo_id="root",
        commit_hash="bad-hash",
    )

    assert result.status == "skipped"
    assert result.message == "目标 commit 不在 origin 目标分支历史内"


@pytest.mark.asyncio
async def test_checkout_future_commit_fast_forwards_without_force(tmp_path: Path):
    repo_path = tmp_path / ".git"
    repo_path.mkdir(parents=True)
    runner = FakeRunner()
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=runner)

    result = await service.checkout_commit(
        repo_id="root",
        commit_hash="future-head",
    )

    assert result.status == "updated"
    assert result.message == "更新至指定版本完成"
    assert any(
        command[1] == ("merge", "--ff-only", "future-head")
        for command in runner.commands
    )


@pytest.mark.asyncio
async def test_force_checkout_resets_to_target_commit(tmp_path: Path):
    (tmp_path / ".git").mkdir(parents=True)
    runner = FakeRunner()
    runner.tracked_dirty = " M pyproject.toml"
    settings = ManagerSettings(project_root=tmp_path)
    service = UpdateManagerService(settings=settings, runner=runner)

    result = await service.checkout_commit(
        repo_id="root",
        commit_hash="future-head",
        force=True,
    )

    assert result.status == "updated"
    assert result.message == "强制更新至指定版本完成"
    assert any(
        command[1] == ("reset", "--hard", "future-head")
        for command in runner.commands
    )


@pytest.mark.asyncio
async def test_add_plugin_clones_upstream_then_sets_origin(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins.txt").write_text("", "utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = []\n[tool.uv.sources]\n",
        "utf-8",
    )
    settings = ManagerSettings(project_root=tmp_path, github_org="PoleSudBot")
    runner = FakeRunner()

    result = await add_plugin(
        runner,
        settings,
        AddPluginRequest(repo_url="https://github.com/source/demo-plugin"),
    )

    assert result.plugin_name == "demo-plugin"
    assert result.package_name == "demo-plugin"
    assert "https://github.com/source/demo-plugin" in (
        tmp_path / "plugins.txt"
    ).read_text("utf-8")
    assert (tmp_path / "plugins" / "demo-plugin" / ".git").exists()
    assert any(
        command[1][:3] == ("remote", "rename", "origin")
        for command in runner.commands
    )
    assert any(command[1][:2] == ("repo", "create") for command in runner.commands)
    assert not any(command[1][:2] == ("repo", "fork") for command in runner.commands)
    assert any(command[1][:2] == ("push", "-u") for command in runner.commands)


@pytest.mark.asyncio
async def test_add_plugin_uses_existing_origin_branch(tmp_path: Path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins.txt").write_text("", "utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = []\n[tool.uv.sources]\n",
        "utf-8",
    )
    settings = ManagerSettings(project_root=tmp_path, github_org="PoleSudBot")
    runner = FakeRunner()
    runner.gh_repo_exists = True
    runner.origin_branch_exists = True

    result = await add_plugin(
        runner,
        settings,
        AddPluginRequest(repo_url="https://github.com/source/demo-plugin"),
    )

    assert result.branch == "L7dev"
    assert not any(command[1][:2] == ("repo", "create") for command in runner.commands)
    assert any(
        command[1] == (
            "fetch",
            "origin",
            "refs/heads/L7dev:refs/remotes/origin/L7dev",
        )
        for command in runner.commands
    )
    assert any(
        command[1] == ("checkout", "-B", "L7dev", "origin/L7dev")
        for command in runner.commands
    )
    assert any(
        command[1] == ("branch", "--set-upstream-to", "origin/L7dev", "L7dev")
        for command in runner.commands
    )
    assert not any(command[1][:2] == ("push", "-u") for command in runner.commands)


def settings_repo(repo_path: Path):
    from zhenxun.services.update_manager.models import ManagedRepo

    return ManagedRepo(
        id="root",
        name="repo",
        kind="root",
        path=repo_path,
        target_branch="dev",
    )


# --- 新增测试：review 修复验证 ---


def test_manager_table_handles_malformed_toml(tmp_path: Path):
    """格式损坏的 manage.toml 不应崩溃，应回退到空配置。"""
    (tmp_path / "manage.toml").write_text("[manager\nkey = ", "utf-8")
    result = _manager_table(tmp_path)
    assert result == {}


def test_persist_survives_io_failure(tmp_path: Path):
    """磁盘写入失败时 _persist 不应抛出异常，应记录错误日志。"""
    from zhenxun.services.update_manager.models import JobRecord

    store = JobStore(tmp_path / "sub" / "jobs.json")
    store.jobs["test"] = JobRecord(kind="update", title="test")

    # 将文件路径指向一个目录，使 write_text (mkdir 会失败，因为目标已是目录)
    (tmp_path / "sub").mkdir(parents=True)
    (tmp_path / "sub" / "jobs.json").mkdir()

    # write_text 会对目录抛出 IsADirectoryError，_persist 应吞掉
    store._persist()


def test_load_discards_corrupt_job_file(tmp_path: Path):
    """损坏的 jobs.json 文件应被静默丢弃，不影响启动。"""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text("not valid json", "utf-8")
    store = JobStore(jobs_file)
    assert store.jobs == {}


def test_load_discards_file_with_unexpected_model_fields(tmp_path: Path):
    """JSON 格式正确但字段不符合模型时，应丢弃旧摘要。"""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(
        json.dumps([{"id": "abc", "kind": "unknown_kind", "state": "running"}]),
        "utf-8",
    )
    store = JobStore(jobs_file)
    # 无效模型数据应被丢弃
    assert "abc" not in store.jobs


@pytest.mark.asyncio
async def test_resolve_default_branch_falls_back_ls_remote():
    """symbolic-ref 和 remote show 都失败时，用 ls-remote 探测 main/master。"""

    class BranchProbeRunner:
        def __init__(self):
            self.call_order: list[str] = []
            self.ls_remote_responses: dict[str, bool] = {}

        async def git(self, cwd, *args, check=True):
            self.call_order.append(" ".join(args))
            if args[0] == "symbolic-ref":
                return CommandResult(("git", *args), 128, "", "not found")
            if args[:2] == ("remote", "show"):
                return CommandResult(("git", *args), 128, "", "no remote show")
            if args[0] == "ls-remote" and args[1] == "--heads":
                target = args[3] if len(args) > 3 else ""
                if target == "refs/heads/main" and self.ls_remote_responses.get("main"):
                    return CommandResult(
                        ("git", *args),
                        0,
                        "abc123\trefs/heads/main",
                    )
                if target == "refs/heads/master" and self.ls_remote_responses.get(
                    "master"
                ):
                    return CommandResult(
                        ("git", *args),
                        0,
                        "abc123\trefs/heads/master",
                    )
                return CommandResult(("git", *args), 0, "")
            raise AssertionError(f"unexpected: {args}")

    runner = BranchProbeRunner()
    runner.ls_remote_responses = {"main": True, "master": False}

    branch = await resolve_remote_default_branch(runner, Path("."), "origin")
    assert branch == "main"

    runner2 = BranchProbeRunner()
    runner2.ls_remote_responses = {"main": False, "master": True}

    branch = await resolve_remote_default_branch(runner2, Path("."), "origin")
    assert branch == "master"

    runner3 = BranchProbeRunner()
    runner3.ls_remote_responses = {"main": False, "master": False}

    branch = await resolve_remote_default_branch(runner3, Path("."), "origin")
    assert branch == "main"


def test_session_expires_after_ttl(monkeypatch):
    """会话过期后 valid() 应返回 False 并清理过期条目。"""
    store = UpdateSessionStore()
    client_ip = store.client_ip({}, "10.0.0.1")
    session = store.create(client_ip)

    assert store.valid(client_ip, session)

    # 模拟时间过去 25 小时
    future = __import__("time").time() + 90000
    monkeypatch.setattr("time.time", lambda: future)

    assert not store.valid(client_ip, session)
    # 过期后应被清理
    assert client_ip not in store._sessions


def test_session_valid_still_works_with_fresh_entry():
    """刚创建的会话在有效期内正常通过校验。"""
    store = UpdateSessionStore()
    ip = "192.168.1.1"
    token = store.create(ip)
    assert store.valid(ip, token)
    assert not store.valid(ip, "wrong-token")


@pytest.mark.asyncio
async def test_git_runner_marks_cwd_as_safe_directory(tmp_path: Path):
    """update_manager 执行 git 时应临时信任当前受管仓库目录。"""
    runner = AsyncCommandRunner()
    calls = []

    async def fake_run(args, cwd, *, timeout=None, check=True):
        calls.append((args, cwd, timeout, check))
        return CommandResult(tuple(args), 0)

    runner.run = fake_run  # type: ignore[method-assign]

    await runner.git(tmp_path, "fetch", "origin", "--prune")

    assert calls == [
        (
            [
                "git",
                "-c",
                f"safe.directory={tmp_path.resolve(strict=False).as_posix()}",
                "fetch",
                "origin",
                "--prune",
            ],
            tmp_path,
            runner.git_timeout,
            True,
        )
    ]


@pytest.mark.asyncio
async def test_gh_runner_does_not_inject_git_safe_directory(tmp_path: Path):
    """safe.directory 是 Git 专用兼容参数，不能注入 gh CLI 调用。"""
    runner = AsyncCommandRunner()
    calls = []

    async def fake_run(args, cwd, *, timeout=None, check=True):
        calls.append((args, cwd, timeout, check))
        return CommandResult(tuple(args), 0)

    runner.run = fake_run  # type: ignore[method-assign]

    await runner.gh(tmp_path, "repo", "view", "owner/repo")

    assert calls == [
        (
            ["gh", "repo", "view", "owner/repo"],
            tmp_path,
            runner.git_timeout,
            True,
        )
    ]
