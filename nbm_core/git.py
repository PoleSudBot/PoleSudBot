# nbm_core/git.py
"""
High-level, business-oriented functions for Git and GitHub operations.

This module abstracts away the raw command-line arguments and provides
a clear, intention-driven API for interacting with repositories.
It relies on the `process` module for actual command execution.
"""

import json
import re
from pathlib import Path

from . import config
from .process import gh, git


def get_repo_name_from_url(url: str) -> str | None:
    """
    Extracts the 'owner/repo' name from a GitHub URL.

    Args:
        url: The full GitHub URL (e.g., https://github.com/owner/repo.git).

    Returns:
        The 'owner/repo' string, or None if not found.
    """
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(\.git)?$", url)
    return match.group(1) if match else None


def get_default_branch(repo_full_name: str) -> str:
    """
    Fetches the default branch name of a remote repository using the GitHub CLI.

    Args:
        repo_full_name: The full repository name in 'owner/repo' format.

    Returns:
        The name of the default branch, defaulting to 'main' on failure.
    """
    try:
        data_str = gh(
            ["repo", "view", repo_full_name, "--json", "defaultBranchRef"],
            cwd=config.PROJECT_ROOT,
            check=False,  # Manually handle failure for graceful fallback
            quiet=True,
        )
        if data_str and (data := json.loads(data_str)):
            return data["defaultBranchRef"]["name"]
    except (json.JSONDecodeError, KeyError):
        config.logger.warning(
            f"Could not parse default branch for {repo_full_name}. "
            "Falling back to 'main'."
        )
    except Exception as e:
        config.logger.warning(f"An unexpected error occurred: {e}")

    return "main"


def get_current_branch(cwd: Path) -> str:
    """
    Gets the current active branch name in a local repository.

    Args:
        cwd: The path to the local repository.

    Returns:
        The current branch name.
    """
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, quiet=True)


def is_workspace_dirty(cwd: Path) -> bool:
    """
    Checks if the Git working directory has any uncommitted changes.

    Args:
        cwd: The path to the local repository.

    Returns:
        True if there are uncommitted changes, False otherwise.
    """
    # The --porcelain flag ensures the output is stable for scripting.
    # An empty output means the workspace is clean.
    status_output = git(["status", "--porcelain"], cwd=cwd, quiet=True)
    return bool(status_output)
