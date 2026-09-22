import itertools

import pytest

from alexa_sync.sync import Item, Pair, TooManyDeletes, sync

_ids = itertools.count()


class FakeSide:
    def __init__(self, label, items=(), fail=False):
        self.label = label
        self.items = {i.id: i for i in items}
        self.fail = fail

    def fetch(self):
        return dict(self.items)

    def add(self, name, completed):
        if self.fail:
            raise RuntimeError("boom")
        item = Item(f"{self.label}-{next(_ids)}", name, completed)
        self.items[item.id] = item
        return item.id

    def update(self, item_id, *, name, completed):
        if self.fail:
            raise RuntimeError("boom")
        old = self.items[item_id]
        self.items[item_id] = Item(
            item_id,
            old.name if name is None else name,
            old.completed if completed is None else completed,
        )

    def delete(self, item_id):
        if self.fail:
            raise RuntimeError("boom")
        del self.items[item_id]

    def names(self):
        return sorted((i.name, i.completed) for i in self.items.values())


def test_initial_sync_copies_active_items_both_ways_and_skips_completed():
    a = FakeSide("alexa", [Item("a1", "milk", False), Item("a2", "old eggs", True)])
    r = FakeSide("rem", [Item("r1", "Bread", False), Item("r2", "done thing", True)])

    pairs = sync(a, r, [])

    assert a.names() == [("Bread", False), ("milk", False), ("old eggs", True)]
    assert r.names() == [("Bread", False), ("Milk", False), ("done thing", True)]
    assert len(pairs) == 2


def test_initial_sync_pairs_existing_duplicates_by_name():
    a = FakeSide("alexa", [Item("a1", "paper towels", False)])
    r = FakeSide("rem", [Item("r1", "Paper  Towels", False)])

    pairs = sync(a, r, [])

    assert pairs == [Pair("a1", "r1", "paper towels", False)]
    assert len(a.items) == len(r.items) == 1


def test_completion_propagates_each_way():
    a = FakeSide("alexa", [Item("a1", "milk", True), Item("a2", "eggs", False)])
    r = FakeSide("rem", [Item("r1", "Milk", False), Item("r2", "Eggs", True)])
    pairs = [Pair("a1", "r1", "milk", False), Pair("a2", "r2", "eggs", False)]

    pairs = sync(a, r, pairs)

    assert r.items["r1"].completed and a.items["a2"].completed
    assert all(p.completed for p in pairs)


def test_uncompleting_propagates():
    a = FakeSide("alexa", [Item("a1", "milk", True)])
    r = FakeSide("rem", [Item("r1", "Milk", False)])

    sync(a, r, [Pair("a1", "r1", "milk", True)])

    assert not a.items["a1"].completed


def test_rename_propagates_and_case_only_difference_is_ignored():
    a = FakeSide("alexa", [Item("a1", "milk", False)])
    r = FakeSide("rem", [Item("r1", "Oat milk", False)])
    pairs = sync(a, r, [Pair("a1", "r1", "milk", False)])
    assert a.items["a1"].name == "Oat milk"

    # Alexa's lowercase vs our capitalized display must not look like a rename.
    a.items["a1"] = Item("a1", "oat milk", False)
    before = dict(r.items)
    sync(a, r, pairs)
    assert r.items == before


def test_deletes_propagate_each_way():
    a = FakeSide("alexa", [Item("a1", "milk", False)])
    r = FakeSide("rem", [Item("r2", "Eggs", False)])
    pairs = [Pair("a1", "r1", "milk", False), Pair("a2", "r2", "eggs", False)]

    pairs = sync(a, r, pairs)

    assert a.items == {} and r.items == {} and pairs == []


def test_conflict_alexa_wins():
    a = FakeSide("alexa", [Item("a1", "whole milk", True)])
    r = FakeSide("rem", [Item("r1", "Skim milk", False)])

    (pair,) = sync(a, r, [Pair("a1", "r1", "milk", False)])

    assert r.items["r1"] == Item("r1", "Whole milk", True)
    assert pair == Pair("a1", "r1", "whole milk", True)


def test_mass_delete_guard():
    pairs = [Pair(f"a{i}", f"r{i}", f"x{i}", False) for i in range(10)]
    r = FakeSide("rem", [Item(f"r{i}", f"X{i}", False) for i in range(10)])

    with pytest.raises(TooManyDeletes):
        sync(FakeSide("alexa"), r, pairs)
    assert len(r.items) == 10


def test_failed_update_keeps_old_snapshot_for_retry():
    a = FakeSide("alexa", [Item("a1", "milk", True)])
    r = FakeSide("rem", [Item("r1", "Milk", False)], fail=True)
    old = Pair("a1", "r1", "milk", False)

    assert sync(a, r, [old]) == [old]

    r.fail = False
    assert sync(a, r, [old]) == [Pair("a1", "r1", "milk", True)]
    assert r.items["r1"].completed


def test_failed_add_is_retried_next_run():
    a = FakeSide("alexa", [Item("a1", "milk", False)])
    r = FakeSide("rem", fail=True)

    assert sync(a, r, []) == []
    r.fail = False
    assert len(sync(a, r, [])) == 1
