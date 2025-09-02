# nbm_core/exceptions.py
"""Defines custom exceptions for the nbm application."""

import subprocess
from typing import Union

# 定义一个类型别名，用于表示所有可能的子进程结果
CmdResult = Union[
    subprocess.CompletedProcess,
    subprocess.TimeoutExpired,
    subprocess.CalledProcessError,
]


class CommandError(Exception):
    """
    Raised when an external command fails.

    This exception wraps the original subprocess result, allowing for
    detailed inspection of the failure.
    """

    def __init__(self, message: str, result: CmdResult | None = None):
        super().__init__(message)
        self.result = result
