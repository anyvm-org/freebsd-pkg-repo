import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import ledger
import mkshards

NOW = "2026-09-02T00:00:00Z"
REPO = "anyvm-org/freebsd-pkg-repo"
SLUG = "FreeBSD-15-riscv64"


def fresh(origins):
    return ledger.new_ledger("FreeBSD:15:riscv64", "abc123", origins)


class PlanFirstRunTest(unittest.TestCase):

    def test_everything_lands_in_shard_zero(self):
        led = fresh(["net/rsync", "archivers/zstd"])
        built = {"net/rsync": "rsync-3.5.0.pkg",
                 "archivers/zstd": "zstd-1.5.7_2.pkg"}
        plan = mkshards.plan(led, built, NOW)
        self.assertEqual(sorted(plan), [0])
        self.assertEqual(plan[0]["new"],
                         {"rsync-3.5.0.pkg": "rsync-3.5.0.pkg",
                          "zstd-1.5.7_2.pkg": "zstd-1.5.7_2.pkg"})
        self.assertEqual(plan[0]["existing"], [])
        self.assertEqual(plan[0]["delete"], [])

    def test_ledger_records_safe_name_and_shard(self):
        led = fresh(["archivers/liblz4"])
        mkshards.plan(led, {"archivers/liblz4": "liblz4-1.10.0_2,1.pkg"}, NOW)
        entry = led["ports"]["archivers/liblz4"]
        self.assertEqual(entry["state"], ledger.STATE_BUILT)
        self.assertEqual(entry["pkgfile"], "liblz4-1.10.0_2-1.pkg")
        self.assertEqual(entry["shard"], 0)

    def test_unsafe_name_is_staged_under_its_safe_name(self):
        led = fresh(["archivers/liblz4"])
        plan = mkshards.plan(led, {"archivers/liblz4": "liblz4-1.10.0_2,1.pkg"},
                             NOW)
        self.assertEqual(plan[0]["new"],
                         {"liblz4-1.10.0_2-1.pkg": "liblz4-1.10.0_2,1.pkg"})


class PlanSecondRunTest(unittest.TestCase):

    def seeded(self):
        """A ledger where rsync already sits in shard 0."""
        led = fresh(["net/rsync", "shells/bash"])
        mkshards.plan(led, {"net/rsync": "rsync-3.5.0.pkg"}, NOW)
        return led

    def test_new_package_joins_the_open_shard_with_its_neighbours(self):
        led = self.seeded()
        plan = mkshards.plan(led, {"shells/bash": "bash-5.3.pkg"}, NOW)
        self.assertEqual(sorted(plan), [0])
        self.assertEqual(plan[0]["new"], {"bash-5.3.pkg": "bash-5.3.pkg"})
        # the shard index must be regenerated from EVERYTHING in it
        self.assertEqual(plan[0]["existing"], ["rsync-3.5.0.pkg"])

    def test_untouched_shard_is_not_in_the_plan(self):
        led = fresh(["a/a", "b/b"])
        mkshards.plan(led, {"a/a": "a-1.pkg"}, NOW, capacity=1)   # fills 0
        plan = mkshards.plan(led, {"b/b": "b-1.pkg"}, NOW, capacity=1)
        self.assertEqual(sorted(plan), [1])
        self.assertEqual(plan[1]["existing"], [])

    def test_version_bump_in_same_shard_replaces_the_old_file(self):
        led = self.seeded()
        plan = mkshards.plan(led, {"net/rsync": "rsync-3.5.1.pkg"}, NOW)
        self.assertEqual(plan[0]["new"], {"rsync-3.5.1.pkg": "rsync-3.5.1.pkg"})
        self.assertEqual(plan[0]["existing"], [])
        self.assertEqual(plan[0]["delete"], ["rsync-3.5.0.pkg"])
        self.assertEqual(led["ports"]["net/rsync"]["pkgfile"], "rsync-3.5.1.pkg")

    def test_version_bump_into_a_new_shard_leaves_the_old_file_alone(self):
        led = fresh(["a/a", "b/b"])
        mkshards.plan(led, {"a/a": "a-1.pkg"}, NOW, capacity=1)   # a-1 in 0
        plan = mkshards.plan(led, {"a/a": "a-2.pkg"}, NOW, capacity=1)
        self.assertEqual(sorted(plan), [1])
        self.assertEqual(plan[1]["new"], {"a-2.pkg": "a-2.pkg"})
        self.assertEqual(plan[1]["delete"], [])
        self.assertEqual(led["ports"]["a/a"]["shard"], 1)


class ConfTest(unittest.TestCase):

    def test_one_block_per_shard_zero_to_max(self):
        led = fresh(["a/a"])
        led["ports"]["a/a"].update({"state": "built", "pkgfile": "a.pkg",
                                    "shard": 2})
        conf = mkshards.generate_conf(led, REPO, SLUG)
        for i in (0, 1, 2):
            self.assertIn("anyvm-%03d: {" % i, conf)
            self.assertIn("releases/download/pkg-%s-%03d\"" % (SLUG, i), conf)
        self.assertNotIn("anyvm-003", conf)

    def test_block_carries_signature_settings(self):
        led = fresh(["a/a"])
        led["ports"]["a/a"].update({"state": "built", "pkgfile": "a.pkg",
                                    "shard": 0})
        conf = mkshards.generate_conf(led, REPO, SLUG)
        self.assertIn('signature_type: "pubkey"', conf)
        self.assertIn('pubkey: "/usr/local/etc/pkg/keys/anyvm.pub"', conf)

    def test_empty_ledger_still_yields_shard_zero(self):
        conf = mkshards.generate_conf(fresh(["a/a"]), REPO, SLUG)
        self.assertIn("anyvm-000: {", conf)


if __name__ == "__main__":
    unittest.main()
