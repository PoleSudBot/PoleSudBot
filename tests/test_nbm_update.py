from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import nbm
from nbm_core.exceptions import CommandError


def _make_args(**overrides) -> argparse.Namespace:
    values = {
        "scope": nbm.config.DEFAULT_SCOPE,
        "verbose": 0,
        "resume_after_root": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture(autouse=True)
def _stub_target_paths(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(nbm.CommandBase, "_get_target_paths", lambda self: [])


def test_update_stops_when_root_update_fails(monkeypatch: pytest.MonkeyPatch):
    reexec_called = False

    def fake_reexec(**_kwargs):
        nonlocal reexec_called
        reexec_called = True

    monkeypatch.setattr(
        nbm,
        "_update_existing_repo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CommandError("root dirty")),
    )
    monkeypatch.setattr(nbm, "_reexec_latest_nbm", fake_reexec)

    command = nbm.UpdateCommand(_make_args())

    with pytest.raises(CommandError, match="root dirty"):
        command.execute()

    assert reexec_called is False


def test_update_reexecs_after_root_success(monkeypatch: pytest.MonkeyPatch):
    reexec_kwargs: dict[str, object] = {}

    monkeypatch.setattr(nbm, "_update_existing_repo", lambda *_args, **_kwargs: "head")
    monkeypatch.setattr(
        nbm,
        "_reexec_latest_nbm",
        lambda **kwargs: reexec_kwargs.update(kwargs),
    )

    command = nbm.UpdateCommand(_make_args(verbose=2))
    command.execute()

    assert reexec_kwargs == {
        "command": "update",
        "extra_args": ["--resume-after-root"],
        "verbose": 2,
        "scope": nbm.config.DEFAULT_SCOPE,
    }


def test_prod_setup_continues_after_repo_failures_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
):
    uv_calls: list[tuple[str, ...]] = []
    saved_states: list[dict[str, object]] = []
    summaries: list[list[nbm.RepoSyncResult]] = []

    monkeypatch.setattr(nbm.project, "check_and_setup_configs", lambda: None)
    monkeypatch.setattr(nbm, "ensure_venv_exists", lambda: None)
    monkeypatch.setattr(
        nbm,
        "_read_plugin_urls",
        lambda: [
            "https://github.com/example/plugin-a.git",
            "https://github.com/example/plugin-b.git",
        ],
    )
    monkeypatch.setattr(
        nbm,
        "_sync_resources_repo_with_report",
        lambda: nbm.RepoSyncResult(
            label="resources 仓库",
            status="success",
            stage="同步完成",
            head="resources-head",
        ),
    )
    monkeypatch.setattr(
        nbm,
        "_sync_plugin_repositories_with_report",
        lambda _urls: (
            {
                "plugin-a": "plugin-a-head",
                "plugin-b": "plugin-b-old-head",
            },
            [
                nbm.RepoSyncResult(
                    label="插件仓库 plugin-a",
                    status="success",
                    stage="同步完成",
                    head="plugin-a-head",
                ),
                nbm.RepoSyncResult(
                    label="插件仓库 plugin-b",
                    status="failed",
                    stage="快进拉取 origin/dev",
                    head="plugin-b-old-head",
                    error="local changes would be overwritten",
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        nbm,
        "_sync_sidecar_runtime_with_report",
        lambda: (
            "bridge-head",
            "sidecar-core-head",
            {"sidecar-plugin": "sidecar-plugin-head"},
            [
                nbm.RepoSyncResult(
                    label="桥接依赖 nonebot-plugin-genshinuid",
                    status="success",
                    stage="同步完成",
                    head="bridge-head",
                ),
                nbm.RepoSyncResult(
                    label="sidecar Core",
                    status="success",
                    stage="同步完成",
                    head="sidecar-core-head",
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        nbm,
        "_build_lock_fingerprint",
        lambda **_kwargs: ("fingerprint", {"material": "ok"}),
    )
    monkeypatch.setattr(
        nbm,
        "_should_regenerate_lock",
        lambda _fingerprint: (True, "test"),
    )
    monkeypatch.setattr(
        nbm.process,
        "uv_streamed",
        lambda args, _cwd: uv_calls.append(tuple(args)),
    )
    monkeypatch.setattr(
        nbm,
        "_save_prod_setup_state",
        lambda **kwargs: saved_states.append(kwargs),
    )
    monkeypatch.setattr(
        nbm,
        "_log_repo_sync_summary",
        lambda results: summaries.append(list(results)),
    )

    command = nbm.ProdSetupCommand(_make_args())

    with pytest.raises(SystemExit) as exc:
        command.execute()

    assert exc.value.code == 1
    assert uv_calls[0] == ("lock",)
    assert uv_calls[1][0] == "sync"
    assert len(saved_states) == 1
    assert saved_states[0]["plugin_heads"] == {
        "plugin-a": "plugin-a-head",
        "plugin-b": "plugin-b-old-head",
    }
    assert len(summaries) == 1
    assert nbm._collect_repo_sync_failures(summaries[0]) == [
        nbm.RepoSyncResult(
            label="插件仓库 plugin-b",
            status="failed",
            stage="快进拉取 origin/dev",
            head="plugin-b-old-head",
            error="local changes would be overwritten",
        )
    ]


def test_prod_setup_skips_state_save_when_failed_repo_has_no_head(
    monkeypatch: pytest.MonkeyPatch,
):
    saved_states: list[dict[str, object]] = []

    monkeypatch.setattr(nbm.project, "check_and_setup_configs", lambda: None)
    monkeypatch.setattr(nbm, "ensure_venv_exists", lambda: None)
    monkeypatch.setattr(nbm, "_read_plugin_urls", lambda: [])
    monkeypatch.setattr(
        nbm,
        "_sync_resources_repo_with_report",
        lambda: nbm.RepoSyncResult(
            label="resources 仓库",
            status="failed",
            stage="克隆仓库",
            error="network down",
        ),
    )
    monkeypatch.setattr(
        nbm, "_sync_plugin_repositories_with_report", lambda _urls: ({}, [])
    )
    monkeypatch.setattr(
        nbm,
        "_sync_sidecar_runtime_with_report",
        lambda: (
            "bridge-head",
            "sidecar-core-head",
            {},
            [
                nbm.RepoSyncResult(
                    label="桥接依赖 nonebot-plugin-genshinuid",
                    status="success",
                    stage="同步完成",
                    head="bridge-head",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        nbm,
        "_build_lock_fingerprint",
        lambda **_kwargs: ("fingerprint", {"material": "ok"}),
    )
    monkeypatch.setattr(
        nbm,
        "_should_regenerate_lock",
        lambda _fingerprint: (False, "unchanged"),
    )
    monkeypatch.setattr(nbm.process, "uv_streamed", lambda _args, _cwd: None)
    monkeypatch.setattr(
        nbm,
        "_save_prod_setup_state",
        lambda **kwargs: saved_states.append(kwargs),
    )
    monkeypatch.setattr(nbm, "_log_repo_sync_summary", lambda _results: None)

    command = nbm.ProdSetupCommand(_make_args())

    with pytest.raises(SystemExit) as exc:
        command.execute()

    assert exc.value.code == 1
    assert saved_states == []


def test_update_existing_repo_restores_ignored_uv_lock_before_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)

    git_calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        nbm,
        "_parse_tracked_status",
        lambda _cwd: [(" M", "uv.lock")],
    )
    monkeypatch.setattr(
        nbm,
        "_checkout_target_branch",
        lambda _cwd, _branch, _label: git_calls.append(("checkout", _branch)),
    )
    monkeypatch.setattr(nbm.git, "get_head_commit", lambda _cwd: "new-head")

    def fake_git(
        args: list[str],
        _cwd: Path,
        check: bool = True,
        quiet: bool = False,
    ) -> str:
        del check, quiet
        git_calls.append(tuple(args))
        return ""

    monkeypatch.setattr(nbm.process, "git", fake_git)

    head = nbm._update_existing_repo(
        repo_path,
        "dev",
        "主仓库",
        ignored_paths={"uv.lock"},
    )

    assert head == "new-head"
    assert (
        "restore",
        "--source=HEAD",
        "--staged",
        "--worktree",
        "--",
        "uv.lock",
    ) in git_calls
    assert ("fetch", "origin", "--prune") in git_calls
    assert ("pull", "--ff-only", "origin", "dev") in git_calls
