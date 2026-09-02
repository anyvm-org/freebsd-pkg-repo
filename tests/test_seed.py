import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import seed


def entry(state, safe, orig=None):
    e = {"state": state, "pkgfile": safe, "shard": 0, "built_at": "t",
         "fail_count": 0}
    if orig is not None:
        e["pkgfile_orig"] = orig
    return e


class SeedPlanTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.shard = os.path.join(self.tmp, "pkg-X-000")
        os.makedirs(self.shard)
        for name in ("liblz4-1.10.0_2-1.pkg", "tree-2.3.2.pkg"):
            open(os.path.join(self.shard, name), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_restores_the_original_name(self):
        led = {"ports": {"archivers/liblz4": entry(
            "built", "liblz4-1.10.0_2-1.pkg", "liblz4-1.10.0_2,1.pkg")}}
        self.assertEqual(
            seed.seed_plan(led, [self.shard]),
            [(os.path.join(self.shard, "liblz4-1.10.0_2-1.pkg"),
              "liblz4-1.10.0_2,1.pkg")])

    def test_old_ledger_entry_without_orig_uses_the_safe_name(self):
        led = {"ports": {"sysutils/tree": entry("built", "tree-2.3.2.pkg")}}
        self.assertEqual(
            seed.seed_plan(led, [self.shard]),
            [(os.path.join(self.shard, "tree-2.3.2.pkg"), "tree-2.3.2.pkg")])

    def test_missing_file_is_skipped_not_fatal(self):
        led = {"ports": {"x/gone": entry("built", "gone-1.pkg")}}
        self.assertEqual(seed.seed_plan(led, [self.shard]), [])

    def test_only_built_entries_are_seeded(self):
        led = {"ports": {"x/p": entry("pending", "tree-2.3.2.pkg")}}
        self.assertEqual(seed.seed_plan(led, [self.shard]), [])


def can_symlink():
    tmp = tempfile.mkdtemp()
    try:
        os.symlink("a", os.path.join(tmp, "b"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        shutil.rmtree(tmp)


@unittest.skipUnless(can_symlink(), "needs symlink support")
class SeedLayoutTest(unittest.TestCase):
    """The layout poudriere's prepare_build accepts without converting:
    .real_<epoch>/All, .latest -> .real_<epoch>, All -> .latest/All."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "rv64-default")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_fresh_root_gets_the_committed_layout(self):
        all_dir = seed.seed_layout(self.root, 1700000000)
        self.assertEqual(
            all_dir, os.path.join(self.root, ".real_1700000000", "All"))
        self.assertTrue(os.path.isdir(all_dir))
        self.assertEqual(os.readlink(os.path.join(self.root, ".latest")),
                         ".real_1700000000")
        self.assertEqual(os.readlink(os.path.join(self.root, "All")),
                         os.path.join(".latest", "All"))
        # the symlink chain resolves to the real All directory
        self.assertTrue(
            os.path.samefile(os.path.join(self.root, "All"), all_dir))

    def test_no_building_directory_is_created(self):
        seed.seed_layout(self.root, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".building")))

    def test_existing_latest_is_reused(self):
        os.makedirs(os.path.join(self.root, ".real_5", "All"))
        os.symlink(".real_5", os.path.join(self.root, ".latest"))
        all_dir = seed.seed_layout(self.root, 9)
        self.assertTrue(os.path.samefile(
            all_dir, os.path.join(self.root, ".real_5", "All")))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".real_9")))


if __name__ == "__main__":
    unittest.main()
