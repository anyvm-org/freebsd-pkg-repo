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
