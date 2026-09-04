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
        done.stderr = ""
        if argv[:3] == ["gh", "release", "view"]:
            done.returncode = self.view_rc
            done.stdout = self.view_out
        elif argv[:3] in (["gh", "release", "download"], ["gh", "release", "upload"]):
            self.downloads += 1
            done.returncode = self.download_rcs.pop(0)
            done.stdout = ""
            done.stderr = (getattr(self, "stderr_text", "HTTP 502")
                           if done.returncode else "")
        else:
            raise AssertionError("unexpected command %r" % (argv,))
        return done


class UploadBatchTest(unittest.TestCase):

    def tearDown(self):
        publish.run = self.real_run

    def test_transient_upload_failure_is_retried(self):
        self.real_run = publish.run
        fake = FakeRun(0, "", [1, 0])
        publish.run = fake
        publish.upload_batch("o/r", "pkg-X-001", ["a.pkg", "b.pkg"], sleep=lambda s: None)
        self.assertEqual(fake.downloads, 2)

    def test_persistent_upload_failure_raises(self):
        self.real_run = publish.run
        publish.run = FakeRun(0, "", [1] * publish.UPLOAD_ATTEMPTS)
        with self.assertRaises(RuntimeError):
            publish.upload_batch("o/r", "pkg-X-001", ["a.pkg"], sleep=lambda s: None)

    def test_rate_limit_waits_long(self):
        self.real_run = publish.run
        waits = []
        fake = FakeRun(0, "", [1, 0])
        fake.stderr_text = "HTTP 403: API rate limit exceeded for installation"
        publish.run = fake
        publish.upload_batch("o/r", "pkg-X-001", ["a.pkg"], sleep=waits.append)
        self.assertEqual(waits, [publish.RATE_LIMIT_WAIT])

    def test_backoff_grows(self):
        self.real_run = publish.run
        waits = []
        publish.run = FakeRun(0, "", [1, 1, 1, 0])
        publish.upload_batch("o/r", "pkg-X-001", ["a.pkg"], sleep=waits.append)
        self.assertEqual(waits, list(publish.UPLOAD_BACKOFF[:3]))


class StillToUploadTest(unittest.TestCase):

    def test_present_files_with_the_same_size_are_skipped(self):
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "a.pkg"); open(a, "wb").write(b"x" * 10)
            b = os.path.join(tmp, "b.pkg"); open(b, "wb").write(b"y" * 20)
            d = os.path.join(tmp, "data.pkg"); open(d, "wb").write(b"z" * 5)
            present = {"a.pkg": 10, "b.pkg": 99, "data.pkg": 4}
            self.assertEqual(publish.still_to_upload([a, b, d], present), [b, d])
            self.assertEqual(publish.still_to_upload([a, b], {}), [a, b])
        finally:
            shutil.rmtree(tmp)

    def test_existing_assets_parses_name_and_size(self):
        real = publish.run
        try:
            class R(object):
                returncode = 0
                stdout = "a.pkg\t10\nweird name.pkg\t7\n"
            publish.run = lambda argv, check=True: R()
            self.assertEqual(publish.existing_assets("o/r", "t"),
                             {"a.pkg": 10, "weird name.pkg": 7})
        finally:
            publish.run = real


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
