"""Load bundled Set 19 artwork once; no runtime network or Pillow dependency."""
import json
from pathlib import Path
import tkinter as tk

COST_COLORS = {1: '#9ca3af', 2: '#4ade80', 3: '#60a5fa', 4: '#c084fc', 5: '#fbbf24', 7: '#fb7185'}


def portrait_badges(canvas, left, top, width, height, cost, stars, tags=()):
    """Draw readable planned-rank/cost badges without adding event targets."""
    if width < 28 or height < 28:
        return
    font_size = 7 if width < 48 else 9
    badges = (
        (left + 1, top + 1, f'{cost}g', 'nw', COST_COLORS[cost]),
        (left + width / 2, top + height - 1, '★' * stars, 's', '#fbbf24'),
    )
    for x, y, text, anchor, color in badges:
        item = canvas.create_text(x, y, text=text, anchor=anchor, fill=color,
                                  font=('Segoe UI', -font_size, 'bold'), tags=tags)
        size = font_size
        bounds = canvas.bbox(item)
        available = width // 2 - 3 if anchor == 'nw' else width - 2
        while bounds[2] - bounds[0] > available and size > 5:
            size -= 1
            canvas.itemconfigure(item, font=('Segoe UI', -size, 'bold'))
            bounds = canvas.bbox(item)
        background = canvas.create_rectangle(
            max(left, bounds[0] - 1), max(top, bounds[1]),
            min(left + width, bounds[2] + 1), min(top + height, bounds[3]),
            fill='#111827', outline='', tags=tags)
        canvas.tag_lower(background, item)


class Portrait(tk.Canvas):
    """One image and its badges share a single mouse/keyboard hit target."""
    def __init__(self, parent, image, cost, stars=1, bg='#192337'):
        super().__init__(parent, width=image.width(), height=image.height(), bg=bg,
                         borderwidth=0, highlightthickness=0)
        self._preserve_text = True
        self.image = image
        self.cost = cost
        self.stars = stars
        self._image_item = self.create_image(0, 0, image=image, anchor='nw')
        self._dim_item = self.create_rectangle(0, 0, image.width(), image.height(),
                                               fill='#192337', outline='', stipple='gray50',
                                               state='hidden', tags='search-dim')
        self._dimmed = False
        self.set_stars(stars)

    def set_stars(self, stars):
        self.stars = stars
        self.delete('portrait-badges')
        portrait_badges(self, 0, 0, self.image.width(), self.image.height(),
                        self.cost, stars, tags='portrait-badges')

    def set_image(self, image):
        """Reuse this target and its canvas items as the fisheye changes size."""
        if image is self.image:
            return
        previous_size = (self.image.width(), self.image.height())
        self.image = image
        self.itemconfigure(self._image_item, image=image)
        size = (image.width(), image.height())
        if size != previous_size:
            self.configure(width=size[0], height=size[1])
            self.coords(self._dim_item, 0, 0, *size)
            self.set_stars(self.stars)
        self.tag_raise(self._dim_item)

    def set_dimmed(self, dimmed):
        if self._dimmed != dimmed:
            self._dimmed = dimmed
            self.itemconfigure(self._dim_item, state='normal' if dimmed else 'hidden')
        self.tag_raise(self._dim_item)


class Artwork:
    def __init__(self, root: tk.Misc, directory: Path):
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self.champions = {}
        self.portraits = {}
        self.traits = {}
        self.trait_dark = {}
        self.unlock_icon = tk.PhotoImage(master=root, file=str(directory / 'unlock-badge.png'))
        for name, asset in manifest['champions'].items():
            self.champions[name] = tk.PhotoImage(master=root, file=str(directory / asset['small']))
            self.portraits[name] = tk.PhotoImage(master=root, file=str(directory / asset['large']))
        for name, asset in manifest['traits'].items():
            path = directory / asset['file']
            self.traits[name] = tk.PhotoImage(master=root, file=str(path))
            self.trait_dark[name] = tk.PhotoImage(master=root, file=str(path.with_name(path.stem + '-dark.png')))

    def mark_unlockables(self, champions):
        """Stamp both cached portrait sizes once, covering every portrait surface."""
        for champion in champions:
            if champion['unlockable']:
                for images in (self.champions, self.portraits):
                    portrait = images[champion['name']]
                    portrait.tk.call(str(portrait), 'copy', str(self.unlock_icon),
                                     '-to', max(0, portrait.width() - self.unlock_icon.width() - 1),
                                     1, '-compositingrule', 'overlay')
