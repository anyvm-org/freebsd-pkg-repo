import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import wantlist


def built(safe, index, orig=None):
    e = {"state": "built", "pkgfile": safe, "shard": index, "built_at": "t",
         "fail_count": 0}
    if orig:
        e["pkgfile_orig"] = orig
    return e


LED = {"ports": {
    "ports-mgmt/pkg": built("pkg-2.8.4.pkg", 0),
    "devel/git@default": built("git-2.55.0.pkg", 1),
    "archivers/liblz4": built("liblz4-1.10.0_2-1.pkg", 0, "liblz4-1.10.0_2,1.pkg"),
    "net/rsync": {"state": "pending", "pkgfile": None, "shard": None},
}}
BASE = "https://github.com/o/r/releases/download/"


class WantlistTest(unittest.TestCase):

    def test_published_queued_packages_get_a_shard_url(self):
        queued = {"ports-mgmt/pkg": "pkg-2.8.4", "net/rsync": "rsync-3.5.0"}
        self.assertEqual(
            wantlist.wanted(LED, queued, "o/r", "S"),
            [(BASE + "pkg-S-000/pkg-2.8.4.pkg", "pkg-2.8.4.pkg")])

    def test_flavored_queue_entry_matches_flavored_ledger_key(self):
        queued = {"devel/git@default": "git-2.55.0"}
        urls = [u for u, _ in wantlist.wanted(LED, queued, "o/r", "S")]
        self.assertEqual(urls, [BASE + "pkg-S-001/git-2.55.0.pkg"])

    def test_original_name_is_restored(self):
        queued = {"archivers/liblz4": "liblz4-1.10.0_2,1"}
        self.assertEqual(
            wantlist.wanted(LED, queued, "o/r", "S"),
            [(BASE + "pkg-S-000/liblz4-1.10.0_2-1.pkg", "liblz4-1.10.0_2,1.pkg")])

    def test_flavored_queue_entry_falls_back_to_the_bare_ledger_key(self):
        queued = {"ports-mgmt/pkg@x": "pkg-2.8.4"}
        self.assertEqual(len(wantlist.wanted(LED, queued, "o/r", "S")), 1)

    def test_read_queued_takes_two_columns(self):
        path = os.path.join(tempfile.mkdtemp(), "q")
        with open(path, "w") as handle:
            handle.write("devel/git@default git-2.55.0\n"
                         "net/rsync rsync-3.5.0 extra\n\n")
        self.assertEqual(wantlist.read_queued(path),
                         {"devel/git@default": "git-2.55.0",
                          "net/rsync": "rsync-3.5.0"})


if __name__ == "__main__":
    unittest.main()
