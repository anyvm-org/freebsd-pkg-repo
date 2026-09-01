import json
import os
import posixpath
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import repoindex

BASE = ("https://github.com/anyvm-org/freebsd-pkg-repo/releases/download"
        "/idx-FreeBSD-15-riscv64")

ORIGINAL = "bash-5.2.37~2$abcdefgh.pkg"
SAFE = "bash-5.2.37-2-abcdefgh.pkg"
SHARD = "pkg-FreeBSD-15-riscv64-000"

SAFE_NAME_OF = {ORIGINAL: SAFE}
SHARD_TAG_OF = {SAFE: SHARD}


def line_for(repopath):
    return json.dumps({"name": "bash", "version": "5.2.37",
                       "repopath": repopath, "sum": "deadbeef"})


class RewriteLineTest(unittest.TestCase):

    def test_repopath_points_at_the_shard(self):
        out = repoindex.rewrite_line(
            line_for("All/Hashed/" + ORIGINAL), SHARD_TAG_OF, SAFE_NAME_OF)
        self.assertEqual(json.loads(out)["repopath"],
                         "../%s/%s" % (SHARD, SAFE))

    def test_other_fields_survive(self):
        out = repoindex.rewrite_line(
            line_for("All/Hashed/" + ORIGINAL), SHARD_TAG_OF, SAFE_NAME_OF)
        entry = json.loads(out)
        self.assertEqual(entry["name"], "bash")
        self.assertEqual(entry["sum"], "deadbeef")

    def test_flat_all_layout_also_works(self):
        out = repoindex.rewrite_line(
            line_for("All/" + ORIGINAL), SHARD_TAG_OF, SAFE_NAME_OF)
        self.assertEqual(json.loads(out)["repopath"],
                         "../%s/%s" % (SHARD, SAFE))

    def test_unknown_package_is_fatal(self):
        with self.assertRaises(KeyError):
            repoindex.rewrite_line(
                line_for("All/Hashed/ghost-1.0~2$zzzz.pkg"),
                SHARD_TAG_OF, SAFE_NAME_OF)


def normalise_url(url):
    """Resolve '..' in a URL the way an HTTP client does.

    posixpath.normpath must NOT be used on a whole URL: it collapses the
    '//' after the scheme, turning https:// into https:/.  Only the path
    component may be normalised.
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        parts._replace(path=posixpath.normpath(parts.path)))


class JoinedUrlTest(unittest.TestCase):
    """The whole scheme rests on this URL resolving to the shard asset."""

    def test_joined_url_normalises_onto_the_shard_release(self):
        out = repoindex.rewrite_line(
            line_for("All/Hashed/" + ORIGINAL), SHARD_TAG_OF, SAFE_NAME_OF)
        url = BASE + "/" + json.loads(out)["repopath"]
        self.assertEqual(
            normalise_url(url),
            "https://github.com/anyvm-org/freebsd-pkg-repo/releases/download"
            "/" + SHARD + "/" + SAFE)

    def test_normalise_url_helper_keeps_the_scheme_intact(self):
        self.assertEqual(normalise_url("https://h/a/../b"), "https://h/b")


class RewriteStreamTest(unittest.TestCase):

    def test_blank_lines_are_dropped(self):
        lines = [line_for("All/Hashed/" + ORIGINAL), "", "   "]
        out = list(repoindex.rewrite_stream(lines, SHARD_TAG_OF, SAFE_NAME_OF))
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
