import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import result


class ManifestTest(unittest.TestCase):

    def setUp(self):
        self.logdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.logdir, "logs"))

    def tearDown(self):
        shutil.rmtree(self.logdir)

    def write(self, name, text):
        with open(os.path.join(self.logdir, ".poudriere.ports." + name), "w") as h:
            h.write(text)

    def portlog(self, pkgname, origin, ended):
        with open(os.path.join(self.logdir, "logs", pkgname + ".log"), "w") as h:
            h.write("=>> Building %s\nbuild started at now\n" % origin)
            h.write("lots of output\n" * 10)
            if ended:
                h.write("build of %s | %s ended at later\n" % (origin, pkgname))

    def test_built_failed_ignored_and_skipped_by_ignored(self):
        self.write("built", "sysutils/tree tree-2.3.2 13\n")
        self.write("failed", "databases/sqlite3 sqlite3-3.53.4,1 stage stage 100\n"
                             "devel/icu icu-76.1,1 build timeout 7200\n")
        self.write("ignored", "devel/llvm20 llvm20-20.1.8_3 too large\n")
        self.write("skipped", "graphics/mesa-libs mesa-libs-1 llvm20-20.1.8_3\n"
                              "x/y y-1 sqlite3-3.53.4,1\n")
        m = result.manifest(self.logdir)
        self.assertEqual(m["built"], {"sysutils/tree": "tree-2.3.2.pkg"})
        self.assertEqual(m["failed"], ["databases/sqlite3"])
        self.assertEqual(m["oversize"], {"devel/icu": "timeout on the CI runner"})
        # mesa-libs was skipped by an ignored port: ignored; x/y by a
        # failed one: stays pending (not listed anywhere)
        self.assertEqual(m["ignored"], ["devel/llvm20", "graphics/mesa-libs"])

    def test_interrupted_is_a_log_without_a_footer(self):
        self.write("built", "sysutils/tree tree-2.3.2 13\n")
        self.portlog("tree-2.3.2", "sysutils/tree", ended=True)
        self.portlog("boost-libs-1.91.0", "devel/boost-libs", ended=False)
        self.portlog("icu-76.1,1", "devel/icu", ended=False)
        self.write("failed", "devel/icu icu-76.1,1 build timeout 7200\n")
        m = result.manifest(self.logdir)
        self.assertEqual(m["interrupted"], ["devel/boost-libs"])

    def test_empty_logdir_gives_an_empty_manifest(self):
        m = result.manifest(self.logdir)
        self.assertEqual(m, {"built": {}, "failed": [], "ignored": [],
                             "oversize": {}, "interrupted": []})


if __name__ == "__main__":
    unittest.main()
