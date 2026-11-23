"""Responsive, offline Tk rendering of role/range positioning estimates."""
import math
import tkinter as tk

from champion_info import star_text, unlock_text
from visuals import COST_COLORS, portrait_badges
from localization import tr, display_text, localize_tree


PANEL = '#192337'
TEXT = '#e5e7eb'
MUTED = '#9ca3af'
ACCENT = '#c4b5fd'


def trait_support_label(support):
    percent = (support or {}).get('percent')
    return f'Traits {percent:.0f}%' if percent is not None else 'Traits n/a'


def champion_details(champion, stars=1, markers='', reason='', holders=(), support=None):
    """Keep compact portraits explainable, including the underlying sources."""
    attack_range = champion.get('attack_range')
    range_text = f'{attack_range:g} hexes' if isinstance(attack_range, (int, float)) else 'unavailable'
    lines = [f"{champion['name']} · {champion['cost']}g · planned {star_text(stars)}",
             f"Role: {champion.get('role') or 'unavailable'} · Attack range: {range_text}",
             'Traits: ' + ', '.join(champion['traits'])]
    if markers:
        lines.append(markers)
    if champion.get('slots', 1) > 1:
        lines.append(f"Occupies {champion['slots']} team slots; shown once on the board.")
    if holders:
        lines.append('Item option: ' + ' / '.join(holders))
    if champion['name'] == 'Yone':
        lines.append('AD/AP FLEX: choose one AD or AP build, not both.')
    if reason:
        lines.append('Estimated position: ' + reason)
    support = support or {}
    lines.append(trait_support_label(support) + ' · trait-tier activation, not DPS or buff strength')
    for trait in support.get('details', []):
        if trait.get('unique'):
            progress = 'unique · excluded from %'
        else:
            progress = f"tier {trait['tier_index']}/{trait['max_tiers']}"
        reached = trait.get('reached') or 'inactive'
        lines.append(f"{trait['name']}: count {trait['count']} · breakpoint {reached} · {progress}")
    if support.get('note'):
        lines.append(support['note'])
    lines.append(unlock_text(champion))
    for label, key in (('Role source', 'role_source'), ('Range source', 'attack_range_source'),
                       ('Unlock source', 'unlock_source')):
        if champion.get(key):
            lines.append(label + ': ' + champion[key])
    return '\n'.join(lines)


class ChampionTip:
    """One delayed tooltip shared by all hit targets on a champion card/board."""
    def __init__(self, owner):
        self.owner = owner
        self.window = None
        self.job = None
        owner.bind('<Unmap>', lambda _event: self.hide(), add='+')
        owner.bind('<Destroy>', self._destroyed, add='+')

    def bind(self, widget, text):
        widget.bind('<Enter>', lambda event: self.queue(text, event.x_root, event.y_root), add='+')
        widget.bind('<Leave>', lambda _event: self.hide(), add='+')
        widget.bind('<Button-1>', lambda _event: self.hide(), add='+')

    def queue(self, text, x, y):
        self.hide()
        self.job = self.owner.after(350, lambda: self.show(text, x, y))

    def show(self, text, x, y):
        self.job = None
        if not self.owner.winfo_ismapped():
            return
        self.window = tk.Toplevel(self.owner)
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        tk.Label(self.window, text=text, bg='#253047', fg=TEXT, justify='left',
                 wraplength=min(460, self.owner.winfo_screenwidth() - 40), padx=10, pady=8,
                 font=('Segoe UI', 9)).pack()
        localize_tree(self.window)
        self.window.update_idletasks()
        x = max(0, min(x + 12, self.owner.winfo_screenwidth() - self.window.winfo_reqwidth()))
        y = max(0, min(y + 16, self.owner.winfo_screenheight() - self.window.winfo_reqheight()))
        self.window.geometry(f'+{x}+{y}')

    def hide(self):
        if self.job is not None:
            self.owner.after_cancel(self.job)
            self.job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def _destroyed(self, event):
        if event.widget is self.owner:
            self.hide()


class BoardView(tk.Canvas):
    """Show exactly the supplied player-half coordinates, never re-estimate them."""
    def __init__(self, parent, champions, artwork, on_unlock):
        super().__init__(parent, bg=PANEL, highlightthickness=0, width=1, height=1)
        self._preserve_text = True
        self.champions = champions
        self.artwork = artwork
        self.on_unlock = on_unlock
        self.result = {}
        self.collab = set()
        self.locked = set()
        self.stars = {}
        self._small_images = {}
        self.tip = ChampionTip(self)
        self.bind('<Configure>', self._draw)

    def show_comp(self, result, collab, locked, stars):
        self.result = result
        self.collab = set(collab)
        self.locked = set(locked)
        self.stars = dict(result.get('planned_stars', stars))
        self._draw()

    def _draw(self, _event=None):
        self.tip.hide()
        self.delete('all')
        width, height = self.winfo_width(), self.winfo_height()
        if width < 10 or height < 10:
            return
        self.create_text(6, 4, text=display_text(tr('ESTIMATED POSITIONING · enemy / frontline ↑')), anchor='nw',
                         fill=ACCENT, font=('Segoe UI', 8, 'bold'))
        self.create_text(6, height - 3, text=display_text(tr('Role + attack range · not combat statistics · hover / click units')),
                         anchor='sw', fill=MUTED, font=('Segoe UI', 8))
        radius = min(52, (width - 12) / (7.5 * math.sqrt(3)), max(1, height - 44) / 6.5)
        hex_width = math.sqrt(3) * radius
        left = (width - 7.5 * hex_width) / 2
        top = 24 + max(0, (height - 44 - 6.5 * radius) / 2)
        centers = {}
        for row in range(4):
            for column in range(7):
                x = left + hex_width * (column + .5 + .5 * (row % 2))
                y = top + radius * (1 + 1.5 * row)
                centers[row, column] = (x, y)
                points = []
                for corner in range(6):
                    angle = math.radians(60 * corner - 30)
                    points.extend((x + radius * math.cos(angle), y + radius * math.sin(angle)))
                self.create_polygon(points, fill='#202e43' if row == 0 else '#1b293d',
                                    outline='#3b4c64', width=1)
        positions = self.result.get('positioning', [])
        if not positions:
            self.create_text(width / 2, height / 2, text='Positioning metadata unavailable',
                             fill=MUTED, font=('Segoe UI', 9))
        holders = self.result.get('item_holders', {})
        for index, marker in enumerate(positions):
            name = marker['champion']
            champion = self.champions[name]
            x, y = centers[marker['row'], marker['column']]
            tag = f'unit-{index}'
            color = COST_COLORS[champion['cost']]
            image = self.artwork.champions[name]
            size = 32
            if radius < 23:
                if name not in self._small_images:
                    self._small_images[name] = image.subsample(2, 2)
                image, size = self._small_images[name], 16
            image_y = y - (4 if radius >= 31 else 0)
            half = size / 2 + 1
            self.create_rectangle(x - half, image_y - half, x + half, image_y + half,
                                  fill=PANEL, outline=color, width=2, tags=tag)
            self.create_image(x, image_y, image=image, tags=tag)
            portrait_badges(self, x - size / 2, image_y - size / 2, size, size,
                            champion['cost'], self.stars.get(name, 1), tags=tag)
            if champion.get('slots', 1) > 1 and radius >= 23:
                self.create_text(x, image_y + size / 2 + 6,
                                 text=f"{champion['slots']}s", fill=TEXT,
                                 font=('Segoe UI', 6, 'bold'), tags=tag)
            kinds = [kind.upper() for kind, holder in holders.items() if holder == name]
            if kinds:
                text = 'FLEX' if 'FLEX' in kinds else '/'.join(kinds)
                label_y = image_y - size / 2 - 5
                self.create_rectangle(x - 16, label_y - 5, x + 16, label_y + 5,
                                      fill=PANEL, outline='', tags=tag)
                self.create_text(x, label_y, text=display_text(tr(text)), fill=ACCENT,
                                 font=('Segoe UI', 6, 'bold'), tags=tag)
            status = []
            if name in self.collab or name == 'Sona':
                status.append('C = Collab')
            if name in self.locked:
                status.append('L = team locked')
            detail = champion_details(
                champion, self.stars.get(name, 1), ' · '.join(status),
                marker.get('reason', ''), kinds,
                self.result.get('champion_trait_support', {}).get(name))
            self.tag_bind(tag, '<Enter>', lambda event, text=detail: self._enter(event, text))
            self.tag_bind(tag, '<Leave>', lambda _event: self._leave())
            self.tag_bind(tag, '<Button-1>', lambda _event, name=name: self._open(name))

    def _enter(self, event, text):
        self.configure(cursor='hand2')
        self.tip.queue(text, event.x_root, event.y_root)

    def _leave(self):
        self.configure(cursor='')
        self.tip.hide()

    def _open(self, name):
        self.tip.hide()
        self.on_unlock(name)
