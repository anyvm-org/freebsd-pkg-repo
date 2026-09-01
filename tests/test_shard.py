import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import shard


class TagTest(unittest.TestCase):

    def test_shard_tag_is_zero_padded(self):
        self.assertEqual(shard.shard_tag("FreeBSD-15-riscv64", 7),
                         "pkg-FreeBSD-15-riscv64-007")

    def test_shard_tag_beyond_three_digits(self):
        self.assertEqual(shard.shard_tag("FreeBSD-15-riscv64", 1234),
                         "pkg-FreeBSD-15-riscv64-1234")

    def test_index_tag(self):
        self.assertEqual(shard.index_tag("FreeBSD-15-riscv64"),
                         "idx-FreeBSD-15-riscv64")


class AssignShardsTest(unittest.TestCase):

    def test_first_packages_go_to_shard_zero(self):
        assignments, counts = shard.assign_shards({}, ["a.pkg", "b.pkg"])
        self.assertEqual(assignments, {"a.pkg": 0, "b.pkg": 0})
        self.assertEqual(counts, {0: 2})

    def test_rolls_over_at_capacity(self):
        names = ["p%d.pkg" % i for i in range(5)]
        assignments, counts = shard.assign_shards({}, names, capacity=2)
        self.assertEqual([assignments[n] for n in names], [0, 0, 1, 1, 2])
        self.assertEqual(counts, {0: 2, 1: 2, 2: 1})

    def test_existing_assignment_never_moves(self):
        assignments, _ = shard.assign_shards(
            {"old.pkg": 3}, ["old.pkg", "new.pkg"], capacity=2)
        self.assertEqual(assignments["old.pkg"], 3)
        self.assertEqual(assignments["new.pkg"], 3)

    def test_new_packages_top_up_the_last_shard(self):
        assignments, counts = shard.assign_shards(
            {"a.pkg": 0}, ["b.pkg"], capacity=2)
        self.assertEqual(assignments["b.pkg"], 0)
        self.assertEqual(counts, {0: 2})

    def test_default_capacity_leaves_margin_under_github_limit(self):
        self.assertLess(shard.SHARD_CAPACITY, 1000)


if __name__ == "__main__":
    unittest.main()
