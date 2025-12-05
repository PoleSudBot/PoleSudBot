# nbm_core/process.py
"""
The single source of truth for executing external commands (git, gh, uv).

This module provides a robust, type-safe, and well-logged interface
for running subprocesses, ensuring that all external command interactions
are centralized and predictable.
"""

from pathlib import Path
import subprocess

from . import config
from .exceptions import CmdResult, CommandError

# nbm_core/process.py


# nbm_core/process.py


def run(
    cmd: list[str],
    cwd: Path,
    check: bool = True,
    quiet: bool = False,
) -> CmdResult | None:
    """
    Core command executor with robust error handling and logging.
    This version uses Popen and communicate() to prevent I/O deadlocks
    and is fortified against UnboundLocalError.
    """
    if not quiet:
        config.logger.debug(f"🔩 Running: {' '.join(cmd)} in '{cwd}'")

    # --- 修正: 在 try 块外部初始化 proc ---
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
        stdout_data, stderr_data = proc.communicate(timeout=config.COMMAND_TIMEOUT)
        result = subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout_data,
            stderr=stderr_data,
        )

    except FileNotFoundError as e:
        msg = (
            f"Command '{cmd[0]}' not found. Is it installed and in your system's PATH?"
        )
        raise CommandError(msg) from e
    except subprocess.TimeoutExpired as e:
        # --- 修正: 检查 proc 是否已成功创建 ---
        if proc:
            proc.kill()
            # 确保即使超时后，也能获取到已有的输出
            proc.communicate()
        msg = f"Command '{' '.join(cmd)}' timed out after {e.timeout} seconds."
        if check:
            raise CommandError(msg, e)
        if not quiet:
            config.logger.warning(msg)
        return e

    # ... (后续的 returncode 检查逻辑保持不变) ...
    if result.returncode != 0:
        if check:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            details = stderr if stderr else stdout
            msg = (
                f"Command '{' '.join(cmd)}' failed with exit code "
                f"{result.returncode}:\n{details}"
            )
            raise CommandError(msg, result)
        if not quiet:
            config.logger.debug(
                f"Command '{' '.join(cmd)}' failed with non-zero exit code "
                f"{result.returncode} (check=False)."
            )
    return result


def run_and_stream(cmd: list[str], cwd: Path, check: bool = True) -> int:
    """
    Executes a command and streams its stdout/stderr to the logger in real-time.
    This is ideal for long-running commands where progress feedback is needed.

    Returns:
        The process's final return code.

    Raises:
        CommandError: If the command fails and `check` is True.
        FileNotFoundError: If the command is not found.
    """
    config.logger.debug(f"🔩 Streaming: {' '.join(cmd)} in '{cwd}'")
    try:
        # 使用 Popen 进行流式处理
        with subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 将 stderr 合并到 stdout
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            bufsize=1,  # 行缓冲
        ) as proc:
            # 实时读取输出
            if proc.stdout:
                for line in proc.stdout:
                    # 直接使用 logger.info 打印，保持格式一致性
                    # 使用 rstrip() 而不是 strip() 来保留行首的空格，这对于某些进度条很重要
                    config.logger.info(line.rstrip())

            returncode = proc.wait(timeout=config.COMMAND_TIMEOUT)

        if check and returncode != 0:
            msg = f"Command '{' '.join(cmd)}' failed with exit code {returncode}."
            raise CommandError(msg)

        return returncode

    except FileNotFoundError as e:
        msg = (
            f"Command '{cmd[0]}' not found. Is it installed and in your system's PATH?"
        )
        raise CommandError(msg) from e
    except subprocess.TimeoutExpired as e:
        msg = f"Command '{' '.join(cmd)}' timed out after {e.timeout} seconds."
        raise CommandError(msg, e)


def uv_streamed(args: list[str], cwd: Path, check: bool = True) -> int:
    """
    Runs a uv command with real-time streaming output.
    Returns the command's exit code.
    """
    return run_and_stream(["uv", *args], cwd, check=check)


def git(args: list[str], cwd: Path, check: bool = True, quiet: bool = False) -> str:
    """
    Runs a Git command and returns its stripped stdout as a string.
    This function safely handles both string and bytes output from the subprocess.
    """
    result = run(["git", *args], cwd, check=check, quiet=quiet)
    if not result or not result.stdout:
        return ""

    output = result.stdout
    # Perform an explicit type check to satisfy the static analyzer.
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="ignore").strip()

    return output.strip()


def gh(args: list[str], cwd: Path, check: bool = True, quiet: bool = False) -> str:
    """
    Runs a GitHub CLI command and returns its stripped stdout on success.
    This function safely handles both string and bytes output from the subprocess.
    """
    result = run(["gh", *args], cwd, check=check, quiet=quiet)
    if not result or not result.stdout:
        return ""

    output = result.stdout
    # Perform an explicit type check to satisfy the static analyzer.
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="ignore").strip()

    return output.strip()


def uv(
    args: list[str], cwd: Path, check: bool = True, quiet: bool = False
) -> CmdResult | None:
    """
    Runs a uv command.
    Returns the full subprocess result object for detailed inspection.
    """
    return run(["uv", *args], cwd, check=check, quiet=quiet)
