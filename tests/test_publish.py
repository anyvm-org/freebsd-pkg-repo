import os
import sys
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


if __name__ == "__main__":
    unittest.main()
