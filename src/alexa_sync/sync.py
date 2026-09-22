"""Two-way reconciliation between the Alexa shopping list and a Reminders list.

The sync is state-based: we remember what each paired item looked like after the
last successful sync, so on the next run we can tell which side changed. Neither
side exposes reliable modification timestamps through the tools we use, so this
three-way comparison (Alexa now, Reminders now, last synced) is what lets
changes flow in both directions.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    completed: bool


@dataclass
class Pair:
    alexa: str
    reminder: str
    name: str  # normalized name at last sync
    completed: bool

    @classmethod
    def from_dict(cls, d: dict) -> Pair:
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


class Side(Protocol):
    """One end of the sync. Implementations raise on failure."""

    label: str

    def fetch(self) -> dict[str, Item]: ...
    def add(self, name: str, completed: bool) -> str: ...
    def update(self, item_id: str, *, name: str | None, completed: bool | None) -> None: ...
    def delete(self, item_id: str) -> None: ...


class TooManyDeletes(Exception):
    pass


def normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def display(name: str) -> str:
    """Alexa stores voice-added items lowercase ("milk"); make them look normal."""
    name = " ".join(name.split())
    return name[:1].upper() + name[1:] if name.islower() else name


@dataclass
class _Update:
    side: Side
    item_id: str
    name: str | None
    completed: bool | None


def sync(alexa: Side, reminders: Side, pairs: list[Pair], max_deletes: int = 5) -> list[Pair]:
    """Run one sync pass and return the new list of pairs to persist."""
    a_items = alexa.fetch()
    r_items = reminders.fetch()

    deletes: list[tuple[Side, str, Pair]] = []
    updates: list[tuple[_Update, Pair, Pair]] = []  # (update, old pair, new pair)
    kept: list[Pair] = []

    for p in pairs:
        a = a_items.pop(p.alexa, None)
        r = r_items.pop(p.reminder, None)
        if a is None and r is None:
            continue
        if a is None:
            deletes.append((reminders, p.reminder, p))
            continue
        if r is None:
            deletes.append((alexa, p.alexa, p))
            continue

        new = Pair(p.alexa, p.reminder, p.name, p.completed)
        a_update = _Update(alexa, a.id, None, None)
        r_update = _Update(reminders, r.id, None, None)

        # Name: Alexa wins if both sides renamed.
        a_name, r_name = normalize(a.name), normalize(r.name)
        if a_name != p.name:
            new.name = a_name
            if r_name != a_name:
                r_update.name = display(a.name)
        elif r_name != p.name:
            new.name = r_name
            a_update.name = r.name

        # Completion: Alexa wins if both sides changed.
        if a.completed != p.completed:
            new.completed = a.completed
            if r.completed != a.completed:
                r_update.completed = a.completed
        elif r.completed != p.completed:
            new.completed = r.completed
            a_update.completed = r.completed

        pending = [u for u in (a_update, r_update) if u.name is not None or u.completed is not None]
        if not pending:
            kept.append(new)
        for i, u in enumerate(pending):
            # Record the new pair only once, against the last update for this item.
            updates.append((u, p, new if i == len(pending) - 1 else None))

    if len(deletes) > max_deletes:
        raise TooManyDeletes(
            f"refusing to delete {len(deletes)} items in one pass (limit {max_deletes}); "
            "run with --max-deletes to allow it"
        )

    # Unpaired items. Completed ones are history, not something to copy over.
    a_new = [a for a in a_items.values() if not a.completed]
    r_new = [r for r in r_items.values() if not r.completed]

    # Pair up identical names first so an initial sync doesn't duplicate items
    # that already exist on both sides.
    r_by_name: dict[str, list[Item]] = {}
    for r in r_new:
        r_by_name.setdefault(normalize(r.name), []).append(r)
    a_unmatched: list[Item] = []
    for a in a_new:
        matches = r_by_name.get(normalize(a.name))
        if matches:
            r = matches.pop(0)
            kept.append(Pair(a.id, r.id, normalize(a.name), False))
        else:
            a_unmatched.append(a)
    r_unmatched = [r for rs in r_by_name.values() for r in rs]

    result = list(kept)

    for side, item_id, p in deletes:
        try:
            side.delete(item_id)
            log.info("deleted %r from %s", p.name, side.label)
        except Exception:
            log.exception("failed to delete %r from %s", p.name, side.label)
            result.append(p)

    failed: set[int] = set()
    for u, old, new in updates:
        try:
            if id(old) not in failed:
                u.side.update(u.item_id, name=u.name, completed=u.completed)
                log.info("updated %r on %s (name=%r, completed=%r)", old.name, u.side.label, u.name, u.completed)
        except Exception:
            log.exception("failed to update %r on %s", old.name, u.side.label)
            failed.add(id(old))
        if new is not None:
            # If any half failed, keep the old snapshot so the change is retried.
            result.append(old if id(old) in failed else new)

    for a in a_unmatched:
        try:
            rid = reminders.add(display(a.name), False)
            result.append(Pair(a.id, rid, normalize(a.name), False))
            log.info("added %r to %s", a.name, reminders.label)
        except Exception:
            log.exception("failed to add %r to %s", a.name, reminders.label)

    for r in r_unmatched:
        try:
            aid = alexa.add(r.name, False)
            result.append(Pair(aid, r.id, normalize(r.name), False))
            log.info("added %r to %s", r.name, alexa.label)
        except Exception:
            log.exception("failed to add %r to %s", r.name, alexa.label)

    return result
