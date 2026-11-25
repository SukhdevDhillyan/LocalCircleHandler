"""Trigger the LocalCircleAutomation “Deploy latest main to dev” workflow."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from sticker_pack import PackError

DEPLOY_REPO = "SukhdevDhillyan/LocalCircleAutomation"
WORKFLOW_FILE = "deploy-latest-main-dev.yml"
ACTIONS_URL = f"https://github.com/{DEPLOY_REPO}/actions/workflows/{WORKFLOW_FILE}"


class DeployError(PackError):
    """Deploy could not be started."""


def trigger_deploy_latest_main() -> dict:
    gh = _gh_path()
    before = _latest_run(gh)
    _run(
        gh,
        "workflow",
        "run",
        WORKFLOW_FILE,
        "--repo",
        DEPLOY_REPO,
        "--ref",
        "main",
    )
    run = _wait_for_new_run(gh, before)
    if run.get("pending"):
        return {
            "message": (
                "Deploy was requested. GitHub has not listed the new run yet. "
                "Watch the workflow page for progress."
            ),
            "run_url": ACTIONS_URL,
            "actions_url": ACTIONS_URL,
        }
    run_url = str(run.get("url") or ACTIONS_URL)
    return {
        "message": (
            "Deploy started on GitHub. It builds LokalCircleCore@main and "
            "LocalCircle@main, then rolls both dev servers. Watch the run for progress."
        ),
        "run_url": run_url,
        "actions_url": ACTIONS_URL,
    }


def _gh_path() -> str:
    found = shutil.which("gh")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "GitHub CLI" / "gh.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    raise DeployError(
        "GitHub CLI (gh) is not installed. Install it, run `gh auth login`, then try again."
    )


def _latest_run(gh: str) -> dict | None:
    rows = _run_json(
        gh,
        "run",
        "list",
        "--repo",
        DEPLOY_REPO,
        "--workflow",
        WORKFLOW_FILE,
        "--limit",
        "1",
        "--json",
        "databaseId,url,status,createdAt",
    )
    return rows[0] if rows else None


def _wait_for_new_run(gh: str, before: dict | None) -> dict:
    before_id = before.get("databaseId") if before else None
    deadline = time.time() + 20
    while time.time() < deadline:
        latest = _latest_run(gh)
        if latest and latest.get("databaseId") != before_id:
            return latest
        time.sleep(1.5)
    return {"url": ACTIONS_URL, "pending": True}


def _run_json(gh: str, *args: str) -> list:
    raw = _run(gh, *args)
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise DeployError("GitHub CLI returned something this page could not read.") from exc
    if not isinstance(data, list):
        raise DeployError("GitHub CLI returned something this page could not read.")
    return data


def _run(gh: str, *args: str) -> str:
    try:
        completed = subprocess.run(
            [gh, *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise DeployError(
            "GitHub CLI (gh) is not installed. Install it, run `gh auth login`, then try again."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DeployError("GitHub CLI timed out. Try again.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        if "auth" in detail.lower() or "login" in detail.lower():
            raise DeployError(f"GitHub CLI is not signed in. Run `gh auth login`, then try again. ({detail})")
        raise DeployError(f"Could not start the deploy workflow. ({detail})")
    return completed.stdout or ""
