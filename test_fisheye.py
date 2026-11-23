"""Fisheye projection must preserve context and accurate selection geometry."""
import unittest

from fisheye import axis, inverse_axis


class FisheyeTests(unittest.TestCase):
    def test_whole_grid_remains_bounded_and_cells_never_invert(self):
        for length in (1, 73, 400, 800):
            for focus in (0, length * .03, length / 2, length * .97, length):
                edges = [axis(index * length / 12, focus, length) for index in range(13)]
                self.assertAlmostEqual(edges[0], 0)
                self.assertAlmostEqual(edges[-1], length)
                self.assertTrue(all(a < b for a, b in zip(edges, edges[1:])))

    def test_neighbors_grow_progressively_while_distant_context_compresses(self):
        edges = [axis(index * 40, 200, 400) for index in range(11)]
        widths = [right - left for left, right in zip(edges, edges[1:])]
        self.assertGreater(widths[4], widths[3])
        self.assertGreater(widths[3], widths[2])
        self.assertGreater(widths[3], 40)
        self.assertLess(widths[0], 40)
        self.assertAlmostEqual(sum(widths), 400)
        self.assertAlmostEqual(widths[4], widths[5])

    def test_projected_clicks_select_the_original_cell(self):
        for focus in (0, 17, 200, 399, 400):
            for index in range(10):
                left, right = (axis(edge, focus, 400) for edge in (index * 40, (index + 1) * 40))
                original = inverse_axis((left + right) / 2, focus, 400)
                self.assertEqual(int(original // 40), index)
                self.assertAlmostEqual(axis(original, focus, 400), (left + right) / 2)


if __name__ == '__main__':
    unittest.main()
