import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import publish


class IsPackageAssetTest(unittest.TestCase):

    def test_real_packages(self):
        for name in ("rsync-3.5.0.pkg", "pkg-2.8.4.pkg",
                     "liblz4-1.10.0_2-1.pkg"):
            self.assertTrue(publish.is_package_asset(name), name)

    def test_index_files_that_also_end_in_pkg(self):
        self.assertFalse(publish.is_package_asset("data.pkg"))
        self.assertFalse(publish.is_package_asset("packagesite.pkg"))

    def test_other_index_files(self):
        for name in ("meta", "meta.conf", "repo.pub", "ledger.json"):
            self.assertFalse(publish.is_package_asset(name), name)


class FakeRun(object):
    """Stands in for publish.run: scripted return codes per command."""

    def __init__(self, view_rc, view_out, download_rcs):
        self.view_rc = view_rc
        self.view_out = view_out
        self.download_rcs = list(download_rcs)
        self.downloads = 0

    def __call__(self, argv, check=True):
        class Done(object):
            pass
        done = Done()
        if argv[:3] == ["gh", "release", "view"]:
            done.returncode = self.view_rc
            done.stdout = self.view_out
        elif argv[:3] == ["gh", "release", "download"]:
            self.downloads += 1
            done.returncode = self.download_rcs.pop(0)
            done.stdout = ""
        else:
            raise AssertionError("unexpected command %r" % (argv,))
        return done


class DownloadAssetsTest(unittest.TestCase):

    def setUp(self):
        self.real_run = publish.run
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        publish.run = self.real_run
        shutil.rmtree(self.tmp)

    def test_missing_release_is_first_run(self):
        publish.run = FakeRun(1, "", [])
        self.assertFalse(publish.download_assets("o/r", "idx-X", "ledger.json",
                                                 self.tmp, sleep=lambda s: None))

    def test_missing_asset_is_first_run(self):
        publish.run = FakeRun(0, "anyvm.conf\nrepo.pub\n", [])
        self.assertFalse(publish.download_assets("o/r", "idx-X", "ledger.json",
                                                 self.tmp, sleep=lambda s: None))

    def test_transient_failure_is_retried_not_taken_for_absence(self):
        fake = FakeRun(0, "ledger.json\n", [1, 1, 0])
        publish.run = fake
        self.assertTrue(publish.download_assets("o/r", "idx-X", "ledger.json",
                                                self.tmp, sleep=lambda s: None))
        self.assertEqual(fake.downloads, 3)

    def test_persistent_failure_raises(self):
        publish.run = FakeRun(0, "ledger.json\n", [1] * publish.DOWNLOAD_ATTEMPTS)
        with self.assertRaises(RuntimeError):
            publish.download_assets("o/r", "idx-X", "ledger.json",
                                    self.tmp, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
