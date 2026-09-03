import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import slice as slicing


ORIGINS = ["net/rsync", "shells/bash", "devel/git", "lang/python312",
           "sysutils/tree", "ftp/curl", "security/sudo", "filesystems/sshfs"]


class SliceTest(unittest.TestCase):

    def test_every_origin_lands_in_exactly_one_slice(self):
        buckets = slicing.assign(ORIGINS, 3)
        flat = sorted(o for b in buckets for o in b)
        self.assertEqual(flat, sorted(ORIGINS))
        self.assertEqual(len(buckets), 3)

    def test_assignment_is_stable_across_calls_and_orderings(self):
        first = slicing.assign(ORIGINS, 4)
        second = slicing.assign(list(reversed(ORIGINS)), 4)
        self.assertEqual(first, second)

    def test_slice_of_is_deterministic_and_in_range(self):
        for origin in ORIGINS:
            index = slicing.slice_of(origin, 16)
            self.assertEqual(index, slicing.slice_of(origin, 16))
            self.assertTrue(0 <= index < 16)

    def test_one_slice_holds_everything(self):
        self.assertEqual(slicing.assign(ORIGINS, 1), [sorted(ORIGINS)])

    def test_all_flavors_of_a_port_share_a_slice(self):
        for k in (2, 7, 16, 18):
            self.assertEqual(slicing.slice_of("devel/llvm20", k),
                             slicing.slice_of("devel/llvm20@lite", k))
            self.assertEqual(slicing.slice_of("devel/git", k),
                             slicing.slice_of("devel/git@tiny", k))

    def test_buckets_are_sorted(self):
        for bucket in slicing.assign(ORIGINS, 2):
            self.assertEqual(bucket, sorted(bucket))


if __name__ == "__main__":
    unittest.main()
