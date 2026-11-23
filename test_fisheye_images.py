"""Rational portrait magnification must not collapse artwork into a few blocks."""
import tkinter as tk
from types import SimpleNamespace
import unittest

from champion_grid import ChampionGrid


class FisheyeImageTests(unittest.TestCase):
    def test_rational_enlargement_preserves_source_detail(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        root.withdraw()
        try:
            image = tk.PhotoImage(master=root, width=56, height=56)
            row = '{' + ' '.join(f'#{index * 4:02x}20c8' for index in range(56)) + '}'
            for y in range(56):
                image.put(row, to=(0, y))
            artwork = SimpleNamespace(champions={'Unit': image}, portraits={'Unit': image})
            grid = ChampionGrid(root, artwork, lambda name: None, lambda name: False)
            enlarged = grid.resized_image('Unit', 92)
            self.assertEqual(enlarged.width(), 92)
            self.assertGreaterEqual(len({enlarged.get(x, 40) for x in range(92)}), 40)
            grid.destroy()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
