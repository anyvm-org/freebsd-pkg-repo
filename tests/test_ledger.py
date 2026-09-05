import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import ledger

NOW = "2026-09-01T12:00:00Z"


def fresh():
    return ledger.new_ledger("FreeBSD:15:riscv64", "abc123",
                             ["net/rsync", "shells/bash", "x11/nope"])


class NewLedgerTest(unittest.TestCase):

    def test_every_origin_starts_pending(self):
        led = fresh()
        self.assertEqual(sorted(ledger.pending_origins(led)),
                         ["net/rsync", "shells/bash", "x11/nope"])

    def test_records_abi_and_commit(self):
        led = fresh()
        self.assertEqual(led["abi"], "FreeBSD:15:riscv64")
        self.assertEqual(led["ports_commit"], "abc123")


class MergeResultTest(unittest.TestCase):

    def test_built_port_leaves_the_pending_queue(self):
        led = fresh()
        ledger.merge_result(led, {"built": {"net/rsync": "rsync-3.4.1.pkg"}},
                            NOW)
        self.assertNotIn("net/rsync", ledger.pending_origins(led))
        entry = led["ports"]["net/rsync"]
        self.assertEqual(entry["state"], ledger.STATE_BUILT)
        self.assertEqual(entry["pkgfile"], "rsync-3.4.1.pkg")
        self.assertEqual(entry["built_at"], NOW)

    def test_one_failure_stays_pending_for_a_retry(self):
        led = fresh()
        ledger.merge_result(led, {"failed": ["shells/bash"]}, NOW)
        self.assertIn("shells/bash", ledger.pending_origins(led))
        self.assertEqual(led["ports"]["shells/bash"]["fail_count"], 1)

    def test_third_failure_is_terminal(self):
        led = fresh()
        for _ in range(ledger.MAX_FAILURES):
            ledger.merge_result(led, {"failed": ["shells/bash"]}, NOW)
        self.assertNotIn("shells/bash", ledger.pending_origins(led))
        self.assertEqual(led["ports"]["shells/bash"]["state"],
                         ledger.STATE_FAILED)

    def test_failed_rebuild_keeps_the_published_package(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["ports-mgmt/pkg"])
        ledger.merge_result(led, {"built": {"ports-mgmt/pkg": "pkg-2.8.4.pkg"}}, NOW)
        ledger.merge_result(led, {"failed": ["ports-mgmt/pkg"]}, NOW)
        entry = led["ports"]["ports-mgmt/pkg"]
        self.assertEqual(entry["state"], "built")
        self.assertEqual(entry["pkgfile"], "pkg-2.8.4.pkg")
        self.assertEqual(entry["fail_count"], 1)
        # and it stays built no matter how often a rebuild fails
        for _ in range(ledger.MAX_FAILURES):
            ledger.merge_result(led, {"failed": ["ports-mgmt/pkg"]}, NOW)
        self.assertEqual(led["ports"]["ports-mgmt/pkg"]["state"], "built")

    def test_ignored_is_recorded_separately_from_failed(self):
        led = fresh()
        ledger.merge_result(led, {"ignored": ["x11/nope"]}, NOW)
        self.assertEqual(led["ports"]["x11/nope"]["state"],
                         ledger.STATE_IGNORED)
        self.assertEqual(led["ports"]["x11/nope"]["fail_count"], 0)

    def test_oversize_is_recorded(self):
        led = fresh()
        ledger.merge_result(led, {"oversize": {"x11/nope": 3 * 1024 ** 3}},
                            NOW)
        self.assertEqual(led["ports"]["x11/nope"]["state"],
                         ledger.STATE_OVERSIZE)

    def test_success_after_a_failure_clears_the_counter(self):
        led = fresh()
        ledger.merge_result(led, {"failed": ["net/rsync"]}, NOW)
        ledger.merge_result(led, {"built": {"net/rsync": "rsync.pkg"}}, NOW)
        self.assertEqual(led["ports"]["net/rsync"]["fail_count"], 0)


class AddOriginsTest(unittest.TestCase):

    def test_new_origins_are_queued_pending(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["sysutils/tree"])
        added = ledger.add_origins(led, ["net/rsync", "sysutils/tree"])
        self.assertEqual(added, ["net/rsync"])
        self.assertEqual(led["ports"]["net/rsync"]["state"], "pending")
        self.assertEqual(ledger.pending_origins(led), ["net/rsync", "sysutils/tree"])
        self.assertFalse(ledger.is_done(led))

    def test_existing_entries_keep_their_state(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["sysutils/tree"])
        ledger.merge_result(led, {"built": {"sysutils/tree": "tree-2.3.2.pkg"}}, NOW)
        ledger.add_origins(led, ["sysutils/tree"])
        self.assertEqual(led["ports"]["sysutils/tree"]["state"], "built")
        self.assertTrue(ledger.is_done(led))

    def test_every_listed_flavor_is_its_own_entry(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", [])
        added = ledger.add_origins(led, ["devel/llvm20", "devel/llvm20@lite"])
        self.assertEqual(added, ["devel/llvm20", "devel/llvm20@lite"])


LISTED = ["devel/git", "devel/git@lite", "devel/llvm20", "devel/llvm20@lite",
          "devel/py-Jinja2", "security/sudo"]


class FlavorTest(unittest.TestCase):
    """The list writes a port's default flavor bare and other flavors with
    @name; poudriere reports the default flavor by its real name."""

    def test_default_flavor_report_keys_as_the_bare_origin(self):
        self.assertEqual(ledger.canonical_origin(LISTED, "devel/git@default"), "devel/git")
        self.assertEqual(ledger.canonical_origin(LISTED, "devel/py-Jinja2@py312"), "devel/py-Jinja2")
        self.assertEqual(ledger.canonical_origin(LISTED, "security/sudo@default"), "security/sudo")

    def test_listed_flavor_keeps_its_name(self):
        self.assertEqual(ledger.canonical_origin(LISTED, "devel/git@lite"), "devel/git@lite")
        self.assertEqual(ledger.canonical_origin(LISTED, "devel/llvm20@lite"), "devel/llvm20@lite")

    def test_unlisted_port_is_left_as_reported(self):
        self.assertEqual(ledger.canonical_origin(LISTED, "x/dep@py312"), "x/dep@py312")
        self.assertEqual(ledger.canonical_origin(LISTED, "x/dep"), "x/dep")

    def test_merge_result_keys_the_default_flavor_onto_the_listed_origin(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", LISTED)
        ledger.merge_result(led, {"built": {"devel/git@default": "git-2.55.0.pkg",
                                           "devel/llvm20@lite": "llvm20-lite-20.1.8_3.pkg"}},
                            NOW, listed=LISTED)
        self.assertEqual(led["ports"]["devel/git"]["state"], "built")
        self.assertNotIn("devel/git@default", led["ports"])
        self.assertEqual(led["ports"]["devel/llvm20@lite"]["state"], "built")
        self.assertEqual(led["ports"]["devel/llvm20"]["state"], "pending")

    def test_canonicalise_migrates_old_keys(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", LISTED)
        ledger.merge_result(led, {"built": {"devel/git@default": "git-2.55.0.pkg"}}, NOW)
        self.assertEqual(ledger.canonicalise(led, LISTED), [("devel/git@default", "devel/git")])
        self.assertEqual(led["ports"]["devel/git"]["pkgfile"], "git-2.55.0.pkg")
        self.assertTrue(all("@default" not in k for k in led["ports"]))

    def test_canonicalise_never_overwrites_a_published_bare_entry(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", LISTED)
        ledger.merge_result(led, {"built": {"devel/git": "git-2.55.0.pkg"}}, NOW)
        ledger.merge_result(led, {"built": {"devel/git@default": "git-2.56.0.pkg"}}, NOW)
        ledger.canonicalise(led, LISTED)
        self.assertEqual(led["ports"]["devel/git"]["pkgfile"], "git-2.55.0.pkg")


class MarkIgnoredTest(unittest.TestCase):

    def test_blacklist_marks_every_flavor_ignored(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", LISTED)
        changed = ledger.mark_ignored(led, ["devel/llvm20"])
        self.assertEqual(changed, ["devel/llvm20", "devel/llvm20@lite"])
        self.assertNotIn("devel/llvm20", ledger.pending_origins(led))
        self.assertIn("devel/git", ledger.pending_origins(led))

    def test_built_entries_are_left_alone(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", LISTED)
        ledger.merge_result(led, {"built": {"devel/llvm20": "llvm20-20.1.8_3.pkg"}}, NOW)
        self.assertEqual(ledger.mark_ignored(led, ["devel/llvm20"]), ["devel/llvm20@lite"])
        self.assertEqual(led["ports"]["devel/llvm20"]["state"], "built")


class InterruptedTest(unittest.TestCase):

    def test_one_interruption_keeps_the_port_pending(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["devel/icu"])
        ledger.merge_result(led, {"interrupted": ["devel/icu"]}, NOW)
        self.assertEqual(led["ports"]["devel/icu"]["state"], "pending")
        self.assertEqual(led["ports"]["devel/icu"]["interrupt_count"], 1)

    def test_second_interruption_makes_it_oversize(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["devel/icu"])
        for _ in range(ledger.MAX_INTERRUPTIONS):
            ledger.merge_result(led, {"interrupted": ["devel/icu"]}, NOW)
        self.assertEqual(led["ports"]["devel/icu"]["state"], "oversize")
        self.assertEqual(ledger.pending_origins(led), [])

    def test_a_built_port_is_not_touched(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["devel/icu"])
        ledger.merge_result(led, {"built": {"devel/icu": "icu-76.1,1.pkg"}}, NOW)
        ledger.merge_result(led, {"interrupted": ["devel/icu"]}, NOW)
        self.assertEqual(led["ports"]["devel/icu"]["state"], "built")

    def test_timeout_reported_as_oversize_leaves_the_queue(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc", ["devel/icu"])
        ledger.merge_result(led, {"oversize": {"devel/icu": "timeout on the CI runner"}}, NOW)
        self.assertEqual(led["ports"]["devel/icu"]["state"], "oversize")


class RequeueTest(unittest.TestCase):

    def test_requeues_only_the_named_states(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc",
                                ["databases/sqlite3", "devel/icu", "sysutils/tree"])
        for _ in range(ledger.MAX_FAILURES):
            ledger.merge_result(led, {"failed": ["databases/sqlite3"]}, NOW)
        ledger.merge_result(led, {"oversize": {"devel/icu": "timeout"},
                                  "built": {"sysutils/tree": "tree-2.3.2.pkg"}}, NOW)
        self.assertEqual(ledger.requeue(led, ["failed"]), ["databases/sqlite3"])
        self.assertEqual(led["ports"]["databases/sqlite3"]["state"], "pending")
        self.assertEqual(led["ports"]["databases/sqlite3"]["fail_count"], 0)
        self.assertEqual(led["ports"]["devel/icu"]["state"], "oversize")
        self.assertEqual(led["ports"]["sysutils/tree"]["state"], "built")


class RequeueTaggedTest(unittest.TestCase):

    def ledger(self):
        led = ledger.new_ledger("FreeBSD:15:riscv64", "abc",
                                ["devel/icu", "graphics/poppler", "sysutils/tree",
                                 "lang/python@py311"])
        ledger.merge_result(led, {"built": {"devel/icu": "icu-76.1-1.pkg",
                                            "sysutils/tree": "tree-2.3.2.pkg"}}, NOW)
        led["ports"]["devel/icu"]["shard"] = 0
        led["ports"]["sysutils/tree"]["shard"] = 0
        for _ in range(ledger.MAX_FAILURES):
            ledger.merge_result(led, {"failed": ["graphics/poppler"]}, NOW)
        return led

    def test_parse(self):
        text = "# comment\ndevel/icu  icu-note-tag  # why\n\nbad\ngraphics/poppler t2\n"
        self.assertEqual(ledger.parse_rebuild_requests(text),
                         {"devel/icu": "icu-note-tag", "graphics/poppler": "t2"})

    def test_published_package_is_flagged_for_replacement(self):
        led = self.ledger()
        changed = ledger.requeue_tagged(led, {"devel/icu": "t1"})
        self.assertEqual(changed, ["devel/icu"])
        entry = led["ports"]["devel/icu"]
        self.assertEqual(entry["state"], "pending")
        self.assertTrue(entry["rebuild"])
        self.assertEqual(entry["pkgfile"], "icu-76.1-1.pkg")
        self.assertEqual(entry["shard"], 0)
        self.assertEqual(entry["rebuild_tag"], "t1")

    def test_failed_port_comes_back_without_a_flag(self):
        led = self.ledger()
        ledger.requeue_tagged(led, {"graphics/poppler": "t1"})
        entry = led["ports"]["graphics/poppler"]
        self.assertEqual(entry["state"], "pending")
        self.assertEqual(entry["fail_count"], 0)
        self.assertNotIn("rebuild", entry)

    def test_same_tag_is_served_once(self):
        led = self.ledger()
        ledger.requeue_tagged(led, {"devel/icu": "t1"})
        ledger.merge_result(led, {"built": {"devel/icu": "icu-76.1-1.pkg"}}, NOW)
        self.assertEqual(ledger.requeue_tagged(led, {"devel/icu": "t1"}), [])
        self.assertEqual(led["ports"]["devel/icu"]["state"], "built")
        self.assertEqual(ledger.requeue_tagged(led, {"devel/icu": "t2"}), ["devel/icu"])

    def test_every_flavor_of_the_origin(self):
        led = self.ledger()
        self.assertEqual(ledger.requeue_tagged(led, {"lang/python": "t1"}),
                         ["lang/python@py311"])

    def test_untouched_ports_stay(self):
        led = self.ledger()
        ledger.requeue_tagged(led, {"devel/icu": "t1"})
        self.assertEqual(led["ports"]["sysutils/tree"]["state"], "built")
        self.assertEqual(led["ports"]["graphics/poppler"]["state"], "failed")


class DoneTest(unittest.TestCase):

    def test_not_done_while_anything_pends(self):
        self.assertFalse(ledger.is_done(fresh()))

    def test_done_when_every_port_is_resolved(self):
        led = fresh()
        ledger.merge_result(led, {
            "built": {"net/rsync": "a.pkg", "shells/bash": "b.pkg"},
            "ignored": ["x11/nope"]}, NOW)
        self.assertTrue(ledger.is_done(led))


class RetargetTest(unittest.TestCase):

    def test_new_tree_gives_failed_ports_another_chance(self):
        led = fresh()
        for _ in range(ledger.MAX_FAILURES):
            ledger.merge_result(led, {"failed": ["shells/bash"]}, NOW)
        ledger.retarget(led, "def456")
        self.assertEqual(led["ports_commit"], "def456")
        self.assertIn("shells/bash", ledger.pending_origins(led))
        self.assertEqual(led["ports"]["shells/bash"]["fail_count"], 0)

    def test_new_tree_does_not_disturb_built_ports(self):
        led = fresh()
        ledger.merge_result(led, {"built": {"net/rsync": "a.pkg"}}, NOW)
        ledger.retarget(led, "def456")
        self.assertEqual(led["ports"]["net/rsync"]["state"],
                         ledger.STATE_BUILT)


if __name__ == "__main__":
    unittest.main()
