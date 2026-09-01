import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import sanitize


class SanitizeAssetNameTest(unittest.TestCase):

    def test_freebsd15_hashed_name(self):
        self.assertEqual(
            sanitize.sanitize_asset_name("zsh-antigen-2.2.3~2$tpjqzziq.pkg"),
            "zsh-antigen-2.2.3-2-tpjqzziq.pkg")

    def test_already_safe_name_is_unchanged(self):
        self.assertEqual(
            sanitize.sanitize_asset_name("py311-setuptools-63.1.0_1.pkg"),
            "py311-setuptools-63.1.0_1.pkg")

    def test_plus_and_comma(self):
        self.assertEqual(
            sanitize.sanitize_asset_name("libsigc++-2.12.1,1~2$abcdefgh.pkg"),
            "libsigc---2.12.1-1-2-abcdefgh.pkg")


class SanitizeAllTest(unittest.TestCase):

    def test_maps_every_input(self):
        self.assertEqual(
            sanitize.sanitize_all(["a~1$x.pkg", "b-2.pkg"]),
            {"a~1$x.pkg": "a-1-x.pkg", "b-2.pkg": "b-2.pkg"})

    def test_repeated_name_is_not_a_collision(self):
        self.assertEqual(
            sanitize.sanitize_all(["a~1$x.pkg", "a~1$x.pkg"]),
            {"a~1$x.pkg": "a-1-x.pkg"})

    def test_distinct_names_colliding_is_fatal(self):
        with self.assertRaises(ValueError):
            sanitize.sanitize_all(["a+b.pkg", "a-b.pkg"])


if __name__ == "__main__":
    unittest.main()
