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


def run(
    cmd: list[str],
    cwd: Path,
    check: bool = True,
    quiet: bool = False,
) -> CmdResult | None:
    """
    Core command executor with robust error handling and logging.
    """
    if not quiet:
        config.logger.debug(f"🔩 Running: {' '.join(cmd)} in '{cwd}'")

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,  # We intend for text output
            encoding="utf-8",
            errors="surrogateescape",  # Handle potential encoding errors gracefully
            timeout=config.COMMAND_TIMEOUT,
        )
    except FileNotFoundError as e:
        msg = (
            f"Command '{cmd[0]}' not found. Is it installed and in your system's PATH?"
        )
        raise CommandError(msg) from e
    except subprocess.TimeoutExpired as e:
        msg = f"Command '{' '.join(cmd)}' timed out after {e.timeout} seconds."
        if check:
            raise CommandError(msg, e)
        if not quiet:
            config.logger.warning(msg)
        return e

    if proc.returncode != 0:
        if check:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            details = stderr if stderr else stdout
            msg = (
                f"Command '{' '.join(cmd)}' failed with exit code "
                f"{proc.returncode}:\n{details}"
            )
            raise CommandError(msg, proc)
        if not quiet:
            config.logger.debug(
                f"Command '{' '.join(cmd)}' failed with non-zero exit code "
                f"{proc.returncode} (check=False)."
            )
    return proc


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
