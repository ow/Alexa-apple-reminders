"""Apple Reminders side of the sync, via the remindctl CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .sync import Item

INSTALL_HINT = "install it with: brew install steipete/tap/remindctl"


class RemindersUnavailable(Exception):
    pass


def find_remindctl() -> str:
    # launchd jobs get a minimal PATH, so also look in both Homebrew prefixes.
    for candidate in (shutil.which("remindctl"), "/opt/homebrew/bin/remindctl", "/usr/local/bin/remindctl"):
        if candidate and Path(candidate).exists():
            return candidate
    raise RemindersUnavailable(f"remindctl not found; {INSTALL_HINT}")


def check_access() -> None:
    """Raise RemindersUnavailable with a fix-it message if we can't use Reminders."""
    proc = subprocess.run([find_remindctl(), "status", "--json"], capture_output=True, text=True, timeout=30)
    try:
        authorized = json.loads(proc.stdout).get("authorized")
    except ValueError:
        authorized = False
    if not authorized:
        raise RemindersUnavailable("no access to Reminders; run: remindctl authorize")


class RemindersSide:
    label = "Reminders"

    def __init__(self, list_name: str) -> None:
        self.list_name = list_name
        self.list_id: str | None = None
        self._bin = find_remindctl()

    def _run(self, *args: str) -> object:
        proc = subprocess.run(
            [self._bin, *args, "--json", "--no-input", "--no-color"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"remindctl {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def resolve(self, pinned_id: str | None) -> str:
        """Find the target list by ID (preferred) or unique name. Never creates one.

        Shared iCloud lists can be missing from EventKit for a moment after a
        process starts, so creating a list whenever the name isn't found would
        make duplicates. Pinning the ID also survives two lists sharing a name.
        """
        lists = self._run("list")
        by_id = {l["id"]: l for l in lists}
        if pinned_id in by_id and by_id[pinned_id]["title"] == self.list_name:
            self.list_id = pinned_id
            return pinned_id
        matches = [l["id"] for l in lists if l["title"] == self.list_name]
        if not matches:
            raise RuntimeError(f"no Reminders list named {self.list_name!r}; create it in Reminders first")
        if len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} Reminders lists are named {self.list_name!r}; rename or delete the extras"
            )
        self.list_id = matches[0]
        return self.list_id

    def fetch(self) -> dict[str, Item]:
        rows = self._run("show", "all", "--list", self.list_name)
        return {r["id"]: Item(r["id"], r["title"], r["isCompleted"]) for r in rows if r["listID"] == self.list_id}

    def add(self, name: str, completed: bool) -> str:
        row = self._run("add", "--title", name, "--list", self.list_name)
        if row["listID"] != self.list_id:
            self._run("delete", row["id"], "--force")
            raise RuntimeError(f"remindctl added {name!r} to list {row['listID']}, not {self.list_id}")
        if completed:
            self._run("edit", row["id"], "--complete")
        return row["id"]

    def update(self, item_id: str, *, name: str | None, completed: bool | None) -> None:
        args = ["edit", item_id]
        if name is not None:
            args += ["--title", name]
        if completed is not None:
            args.append("--complete" if completed else "--incomplete")
        self._run(*args)

    def delete(self, item_id: str) -> None:
        self._run("delete", item_id, "--force")
