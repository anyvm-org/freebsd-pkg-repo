import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import merge


class MergeTest(unittest.TestCase):

    def test_first_job_wins_a_duplicate_dependency(self):
        merged, supplier = merge.merge_results([
            ("build-0", {"built": {"devel/gmake": "gmake-4.4.1.pkg",
                                   "a/x": "x-1.pkg"}}),
            ("build-1", {"built": {"devel/gmake": "gmake-4.4.1.pkg",
                                   "b/y": "y-1.pkg"}}),
        ])
        self.assertEqual(merged["built"], {"devel/gmake": "gmake-4.4.1.pkg",
                                           "a/x": "x-1.pkg", "b/y": "y-1.pkg"})
        self.assertEqual(supplier["gmake-4.4.1.pkg"], "build-0")
        self.assertEqual(supplier["y-1.pkg"], "build-1")

    def test_a_failure_elsewhere_does_not_undo_a_success(self):
        merged, _ = merge.merge_results([
            ("build-0", {"built": {}, "failed": ["lang/perl5.42"]}),
            ("build-1", {"built": {"lang/perl5.42": "perl5-5.42.3.pkg"},
                         "failed": []}),
        ])
        self.assertEqual(merged["failed"], [])
        self.assertIn("lang/perl5.42", merged["built"])

    def test_failed_and_ignored_are_unioned_and_sorted(self):
        merged, _ = merge.merge_results([
            ("build-0", {"failed": ["z/z"], "ignored": ["m/m"]}),
            ("build-1", {"failed": ["a/a"], "ignored": ["m/m", "b/b"]}),
        ])
        self.assertEqual(merged["failed"], ["a/a", "z/z"])
        self.assertEqual(merged["ignored"], ["b/b", "m/m"])

    def test_skipped_is_unioned_and_yields_to_built(self):
        merged, _ = merge.merge_results([
            ("j1", {"skipped": {"a/b": "devel/grpc", "c/d": "devel/grpc"}}),
            ("j2", {"built": {"c/d": "d-1.pkg"}, "skipped": {"a/b": "x/y", "e/f": "x/y"}}),
        ])
        self.assertEqual(merged["skipped"], {"a/b": "devel/grpc", "e/f": "x/y"})

    def test_empty_input(self):
        merged, supplier = merge.merge_results([])
        self.assertEqual(merged, {"built": {}, "failed": [], "ignored": [],
                                  "oversize": {}, "interrupted": [], "skipped": {},
                                  "ignore_reasons": {}})

    def test_ignore_reasons_follow_the_ignored_set(self):
        merged, _ = merge.merge_results([
            ("j1", {"ignored": ["a/b"], "ignore_reasons": {"a/b": "too large", "c/d": "x"}}),
            ("j2", {"built": {"c/d": "d-1.pkg"}}),
        ])
        self.assertEqual(merged["ignore_reasons"], {"a/b": "too large"})
        self.assertEqual(supplier, {})

    def test_interrupted_yields_to_built_or_oversize_elsewhere(self):
        merged, _ = merge.merge_results([
            ("build-0", {"interrupted": ["devel/icu", "math/gmp", "x/y"]}),
            ("build-1", {"built": {"math/gmp": "gmp-6.3.0.pkg"},
                         "oversize": {"devel/icu": "timeout on the CI runner"}}),
        ])
        self.assertEqual(merged["interrupted"], ["x/y"])
        self.assertEqual(list(merged["oversize"]), ["devel/icu"])


if __name__ == "__main__":
    unittest.main()
