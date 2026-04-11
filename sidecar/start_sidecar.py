from __future__ import annotations

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


CONFIG_PATH = Path("/gsuid_core/data/config.json")
DEPENDENCIES_PATH = Path("/sidecar_dependencies.json")
DEFAULT_TRUSTED_IPS = ["localhost", "::1", "127.0.0.1"]
PLAYWRIGHT_BROWSERS_PATH = Path(
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/venv/ms-playwright")
)


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text("utf-8"))


def _resolve_default_gateway() -> str | None:
    route_path = Path("/proc/net/route")
    if not route_path.exists():
        return None

    for line in route_path.read_text("utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) <= 2 or fields[1] != "00000000":
            continue
        gateway = fields[2]
        return ".".join(str(int(gateway[i : i + 2], 16)) for i in range(6, -2, -2))
    return None


def _prepare_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = _load_json_dict(CONFIG_PATH)

    trusted_ips = config.get("TRUSTED_IPS")
    if not isinstance(trusted_ips, list):
        trusted_ips = []

    merged_ips = list(dict.fromkeys([*DEFAULT_TRUSTED_IPS, *trusted_ips]))
    if gateway_ip := _resolve_default_gateway():
        merged_ips = list(dict.fromkeys([*merged_ips, gateway_ip]))

    config["TRUSTED_IPS"] = merged_ips
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=4),
        "utf-8",
    )


def _load_dependency_manifest() -> dict[str, Any]:
    manifest = _load_json_dict(DEPENDENCIES_PATH)
    plugins = manifest.get("plugins", [])
    if not isinstance(plugins, list):
        raise RuntimeError(
            "sidecar dependency manifest is malformed: plugins must be a list"
        )
    return {"plugins": plugins}


def _collect_string_values(data: Any, field_name: str) -> list[str]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise RuntimeError(
            f"sidecar dependency manifest is malformed: {field_name} must be a list"
        )
    values: list[str] = []
    for item in data:
        if not isinstance(item, str):
            raise RuntimeError(
                "sidecar dependency manifest is malformed: "
                f"{field_name} items must be strings"
            )
        value = item.strip()
        if value:
            values.append(value)
    return list(dict.fromkeys(values))


def _collect_runtime_dependencies() -> tuple[list[str], list[str]]:
    manifest = _load_dependency_manifest()
    packages: list[str] = []
    browsers: list[str] = []
    for plugin in manifest["plugins"]:
        if not isinstance(plugin, dict):
            raise RuntimeError(
                "sidecar dependency manifest is malformed: "
                "plugin entries must be objects"
            )
        packages.extend(_collect_string_values(plugin.get("packages"), "packages"))
        browsers.extend(
            _collect_string_values(
                plugin.get("playwright_browsers"),
                "playwright_browsers",
            )
        )
    return list(dict.fromkeys(packages)), list(dict.fromkeys(browsers))


def _is_package_installed(package: str) -> bool:
    try:
        metadata.distribution(package)
    except metadata.PackageNotFoundError:
        return False
    return True


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True, env=os.environ.copy())


def _ensure_python_packages(packages: list[str]) -> None:
    missing = [package for package in packages if not _is_package_installed(package)]
    if not missing:
        return
    print(
        "[sidecar] installing python packages: " + ", ".join(missing),
        flush=True,
    )
    _run_command(["uv", "pip", "install", "--python", sys.executable, *missing])


def _is_playwright_browser_installed(browser: str) -> bool:
    if not PLAYWRIGHT_BROWSERS_PATH.exists():
        return False
    return any(
        path.is_dir() and path.name.startswith(f"{browser}-")
        for path in PLAYWRIGHT_BROWSERS_PATH.iterdir()
    )


def _ensure_playwright_browsers(browsers: list[str]) -> None:
    if not browsers:
        return
    if not _is_package_installed("playwright"):
        raise RuntimeError(
            "playwright browsers requested but the playwright package is not installed"
        )

    missing = [
        browser
        for browser in browsers
        if not _is_playwright_browser_installed(browser)
    ]
    if not missing:
        return

    PLAYWRIGHT_BROWSERS_PATH.mkdir(parents=True, exist_ok=True)
    for browser in missing:
        print(f"[sidecar] installing playwright browser: {browser}", flush=True)
        _run_command([sys.executable, "-m", "playwright", "install", browser])


def _prepare_runtime_dependencies() -> None:
    packages, browsers = _collect_runtime_dependencies()
    _ensure_python_packages(packages)
    _ensure_playwright_browsers(browsers)


def main() -> None:
    _prepare_config()
    _prepare_runtime_dependencies()
    os.execvp(
        "uv",
        [
            "uv",
            "run",
            "--python",
            "/venv/bin/python",
            "core",
            "--host",
            "0.0.0.0",
        ],
    )


if __name__ == "__main__":
    main()
