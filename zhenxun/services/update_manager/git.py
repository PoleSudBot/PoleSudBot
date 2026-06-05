from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re

TOKEN_PATTERN = re.compile(
    r"(https?://)([^/@\s:]+):([^/@\s]+)@|((?:ghp|github_pat|glpat)_[A-Za-z0-9_]+)"
)


@dataclass(frozen=True)
class CommandResult:
    """外部命令执行结果。"""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout.strip() or self.stderr.strip()


class CommandError(RuntimeError):
    """命令失败时保留脱敏后的上下文。"""

    def __init__(self, result: CommandResult):
        self.result = result
        command = " ".join(result.args)
        super().__init__(
            f"{redact(command)} failed with exit code {result.returncode}: "
            f"{redact(result.output)}"
        )


def redact(text: str) -> str:
    """脱敏 URL 凭证与常见 Git token，避免日志泄漏。"""
    return TOKEN_PATTERN.sub(
        lambda m: f"{m.group(1)}***:***@" if m.group(1) else "***",
        text,
    )


class AsyncCommandRunner:
    """异步执行 git/gh，避免阻塞 NoneBot 事件循环。"""

    def __init__(self, *, git_timeout: int = 120, clone_timeout: int = 600):
        self.git_timeout = git_timeout
        self.clone_timeout = clone_timeout

    async def run(
        self,
        args: list[str],
        cwd: Path,
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        """执行外部命令并在超时或失败时返回可诊断结果。"""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CREDENTIAL_HELPER"] = ""
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout or self.git_timeout,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            result = CommandResult(tuple(args), -999, "", "timeout")
            if check:
                raise CommandError(result) from exc
            return result

        result = CommandResult(
            tuple(args),
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
        if check and not result.ok:
            raise CommandError(result)
        return result

    async def git(
        self,
        cwd: Path,
        *args: str,
        timeout: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        """执行 git 子命令。"""
        return await self.run(
            ["git", *args],
            cwd,
            timeout=timeout or self.git_timeout,
            check=check,
        )

    async def gh(
        self,
        cwd: Path,
        *args: str,
        timeout: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        """执行 GitHub CLI 子命令。"""
        return await self.run(
            ["gh", *args],
            cwd,
            timeout=timeout or self.git_timeout,
            check=check,
        )
