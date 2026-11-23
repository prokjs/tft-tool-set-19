"""Portrait metadata must remain inside fixed-pixel artwork at enlarged DPI."""
import tkinter as tk
import unittest

from visuals import Portrait


class PortraitBadgeTests(unittest.TestCase):
    def test_four_stars_and_cost_fit_at_enlarged_display_scales(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        root.withdraw()
        previous_scaling = root.tk.call('tk', 'scaling')
        try:
            for scaling in (1.333, 2.0, 2.666):
                root.tk.call('tk', 'scaling', scaling)
                for size in (32, 56):
                    with self.subTest(scaling=scaling, size=size):
                        image = tk.PhotoImage(master=root, width=size, height=size)
                        portrait = Portrait(root, image, 7, 4)
                        text_items = [item for item in portrait.find_withtag('portrait-badges')
                                      if portrait.type(item) == 'text']
                        self.assertEqual({portrait.itemcget(item, 'text') for item in text_items}, {'7g', '★★★★'})
                        for item in text_items:
                            left, top, right, bottom = portrait.bbox(item)
                            self.assertGreaterEqual(left, 0)
                            self.assertGreaterEqual(top, 0)
                            self.assertLessEqual(right, size)
                            self.assertLessEqual(bottom, size)
                        portrait.destroy()
        finally:
            root.tk.call('tk', 'scaling', previous_scaling)
            root.destroy()


if __name__ == '__main__':
    unittest.main()
