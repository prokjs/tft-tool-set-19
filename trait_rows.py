"""TFT-inspired trait bars. Badge colors indicate breakpoint progress, not Riot's
patch-specific rarity metadata (which is not included in the bundled snapshot).
"""
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from visuals import COST_COLORS

PALETTES = {
    'inactive': {'badge': '#29333e', 'edge': '#526171', 'bar': '#172230', 'text': '#96a2af'},
    'bronze': {'badge': '#ae805b', 'edge': '#ddba91', 'bar': '#3a2d28', 'text': '#f3dfc1'},
    'silver': {'badge': '#aac1c9', 'edge': '#e3f2f4', 'bar': '#293d49', 'text': '#e6f2f6'},
    'gold': {'badge': '#d2ad53', 'edge': '#f9e2a0', 'bar': '#40371f', 'text': '#fff0bd'},
}
STYLE_ORDER = {'gold': 0, 'silver': 1, 'bronze': 2, 'inactive': 3}


def badge_style(trait, definition):
    if not trait['tier']:
        return 'inactive'
    points = definition['breakpoints']
    if trait['unique'] or trait['tier'] == points[-1]:
        return 'gold'
    index = points.index(trait['tier'])
    return 'bronze' if index == 0 else 'silver' if index == 1 else 'gold'


class TraitRow(tk.Canvas):
    def __init__(self, parent, trait, definition, artwork, *, height=44, on_wheel=None, members=()):
        super().__init__(parent, height=height, bg='#192337', highlightthickness=0, takefocus=False)
        self.trait = trait
        self.definition = definition
        self.artwork = artwork
        self.on_wheel = on_wheel
        self.members = members
        self.style_name = badge_style(trait, definition)
        self._fonts = {}
        self._height = None
        self.tip = None
        self.tip_job = None
        self._tip_leave_job = None
        self.bind('<MouseWheel>', self.wheel)
        self.bind('<Enter>', self.queue_tip)
        self.bind('<Leave>', self.leave_tip)
        self.bind('<Configure>', lambda _event: self.hide_tip())
        self.bind('<Unmap>', lambda _event: self.hide_tip())
        self.bind('<Destroy>', self.cleanup, add='+')
        self.set_height(height)

    def _metrics(self, height):
        if height not in self._fonts:
            name_size = 10 if height >= 44 else 9 if height >= 36 else 8
            track_size = 9 if height >= 44 else 8
            self._fonts[height] = (
                tkfont.Font(root=self, family='Segoe UI', size=name_size, weight='bold'),
                tkfont.Font(root=self, family='Segoe UI', size=track_size),
                tkfont.Font(root=self, family='Segoe UI', size=track_size, weight='bold'),
                tkfont.Font(root=self, family='Segoe UI', size=11 if height >= 44 else 9, weight='bold'),
            )
        name_font, track_font, current_font, count_font = self._fonts[height]
        badge_right = 43 if height >= 44 else height
        count = str(self.trait['count']) + ('★' if self.definition.get('count_mode') == 'stars' else '')
        divider = badge_right + max(37 if height >= 44 else 25, count_font.measure(count) + 8)
        text_x = divider + (10 if height >= 44 else 8)
        gap = 12 if height >= 44 else 9
        track_width = sum(max(track_font.measure(str(point)), current_font.measure(str(point))) + 3
                          for point in self.definition['breakpoints'])
        track_width += gap * max(0, len(self.definition['breakpoints']) - 1)
        width = text_x + max(name_font.measure(self.trait['name']), track_width) + 12
        return width, badge_right, divider, text_x, count, gap, self._fonts[height]

    def natural_width(self, height=None):
        """Exact content width: full name and full breakpoint track, never 306px."""
        return self._metrics(self._height if height is None else height)[0]

    def set_height(self, height):
        """Redraw this same canvas in a compact or default size, retaining all data."""
        if self._height == height:
            return
        self.hide_tip()
        self._height = height
        width, badge_right, divider, text_x, count, gap, fonts = self._metrics(height)
        name_font, track_font, current_font, count_font = fonts
        palette = PALETTES[self.style_name]
        self.configure(width=width, height=height)
        self.delete('all')
        center_x = 25 if height >= 44 else (badge_right + 3) / 2
        center_y = height / 2
        half_badge = 18 if height >= 44 else (height - 6) / 2
        self.create_polygon(badge_right, 5, width - 12, 5, width - 3, 12,
                            width - 3, height - 5, badge_right, height - 5,
                            fill=palette['bar'], outline=palette['edge'], width=1)
        self.create_polygon(center_x, 1, center_x + half_badge, height / 4,
                            center_x + half_badge, height * 3 / 4,
                            center_x, height - 1, center_x - half_badge, height * 3 / 4,
                            center_x - half_badge, height / 4,
                            fill=palette['badge'], outline=palette['edge'], width=2)
        image = self.artwork.trait_dark[self.trait['name']] if self.trait['tier'] else self.artwork.traits[self.trait['name']]
        self.create_image(center_x, center_y, image=image)
        self.create_line(divider, 8, divider, height - 8, fill=palette['edge'])
        self.create_text((badge_right + divider) / 2, center_y, text=count, fill=palette['text'],
                         font=count_font, tags='count')
        name_y = 14 if height >= 44 else 11 if height >= 36 else 9
        track_y = height - 13 if height >= 44 else height - 9
        self.create_text(text_x, name_y, text=self.trait['name'], anchor='w',
                         fill=palette['text'], font=name_font, tags='name')
        x = text_x
        for index, point in enumerate(self.definition['breakpoints']):
            if index:
                self.create_text(x, track_y, text='›', anchor='w', fill='#7e8993', font=track_font)
                x += gap
            current = point == self.trait['tier']
            completed = point <= self.trait['count'] and bool(self.trait['tier'])
            self.create_text(x, track_y, text=str(point), anchor='w',
                             fill=palette['text'] if current else '#aaa695' if completed else '#7b8794',
                             font=current_font if current else track_font, tags=f'breakpoint-{point}')
            point_width = max(track_font.measure(str(point)), current_font.measure(str(point))) + 3
            if current:
                self.create_line(x, height - 4, x + point_width - 2, height - 4,
                                 fill=palette['edge'], width=2, tags='active-breakpoint')
            x += point_width

    def queue_tip(self, event):
        self.hide_tip()
        self.tip_job = self.after(400, lambda: self.show_tip(event.x_root, event.y_root))

    def show_tip(self, x=None, y=None):
        """Show sourced effects and the full supplied catalogue, using cached art."""
        self.hide_tip()
        if not self.winfo_ismapped():
            return
        if x is None:
            x = self.winfo_pointerx()
        if y is None:
            y = self.winfo_pointery()
        description = self.definition['description']
        source = self.definition['description_source']
        self.tip = tk.Toplevel(self)
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        self.tip.bind('<Enter>', self.keep_tip)
        self.tip.bind('<Leave>', self.leave_tip)
        background = '#253047'
        wrap = min(440, max(120, self.winfo_screenwidth() - 60))
        canvas = tk.Canvas(self.tip, bg=background, highlightthickness=0, width=wrap + 20, height=1)
        canvas.pack(side='left', fill='both', expand=True)
        content = tk.Frame(canvas, bg=background)
        canvas.create_window(0, 0, window=content, anchor='nw', width=wrap + 20)
        count = str(self.trait['count']) + ('★' if self.definition.get('count_mode') == 'stars' else '')
        tier = f"active breakpoint {self.trait['tier']}" if self.trait['tier'] else 'inactive'
        tk.Label(content, text=f"{self.trait['name']} · {count} · {tier}", justify='left',
                 wraplength=wrap, bg=background, fg='#ffffff', padx=10, pady=7,
                 font=('Segoe UI', 10, 'bold')).pack(fill='x', anchor='w')
        tk.Label(content, text=description, justify='left', wraplength=wrap, bg=background,
                 fg='#ffffff', padx=10, font=('Segoe UI', 10)).pack(fill='x', anchor='w')
        if self.members:
            tk.Label(content, text=f'CHAMPIONS · {len(self.members)}', bg=background,
                     fg='#c4b5fd', anchor='w', padx=10, pady=6,
                     font=('Segoe UI', 9, 'bold')).pack(fill='x')
            catalogue = tk.Frame(content, bg=background)
            catalogue.pack(fill='x', padx=10)
            columns = max(1, wrap // 88)
            tile_width = wrap // columns
            for index, champion in enumerate(self.members):
                name = champion['name']
                tile = tk.Frame(catalogue, bg=background, width=tile_width, height=68)
                tile.grid(row=index // columns, column=index % columns, sticky='nw')
                tk.Label(tile, image=self.artwork.champions[name], bg=background,
                         highlightbackground=COST_COLORS[champion['cost']], highlightthickness=1,
                         borderwidth=0).place(x=(tile_width - 34) // 2, y=0, width=34, height=34)
                tk.Label(tile, text=name, bg=background, fg='#ffffff', wraplength=tile_width - 4,
                         font=('Segoe UI', 8), borderwidth=0).place(x=2, y=35, width=tile_width - 4, height=30)
        tk.Label(content, text='Source: ' + source, justify='left', wraplength=wrap,
                 bg=background, fg='#b9c5d5', padx=10, pady=7,
                 font=('Segoe UI', 8)).pack(fill='x', anchor='w')
        self.tip.update_idletasks()
        content_height = content.winfo_reqheight()
        height = min(content_height, max(80, self.winfo_screenheight() - 40))
        canvas.configure(height=height, scrollregion=(0, 0, wrap + 20, content_height))
        if content_height > height:
            scrollbar = ttk.Scrollbar(self.tip, orient='vertical', command=canvas.yview)
            scrollbar.pack(side='right', fill='y')
            canvas.configure(yscrollcommand=scrollbar.set)
        self._bind_tip_wheel(content, canvas)
        canvas.bind('<MouseWheel>', lambda event: self._scroll_tip(canvas, event))
        self.tip.update_idletasks()
        x = max(0, min(x + 14, self.winfo_screenwidth() - self.tip.winfo_reqwidth()))
        y = max(0, min(y + 18, self.winfo_screenheight() - self.tip.winfo_reqheight()))
        self.tip.geometry(f'+{x}+{y}')

    def _bind_tip_wheel(self, widget, canvas):
        widget.bind('<MouseWheel>', lambda event: self._scroll_tip(canvas, event))
        for child in widget.winfo_children():
            self._bind_tip_wheel(child, canvas)

    @staticmethod
    def _scroll_tip(canvas, event):
        if event.delta and canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def keep_tip(self, _event=None):
        if self._tip_leave_job is not None:
            self.after_cancel(self._tip_leave_job)
            self._tip_leave_job = None

    def leave_tip(self, _event=None):
        # Cancel a pending open immediately, but allow transfer to a tall popup's
        # scrollbar without the popup vanishing as the pointer leaves its row.
        if self.tip_job is not None:
            self.after_cancel(self.tip_job)
            self.tip_job = None
        self.keep_tip()
        if self.tip is not None:
            self._tip_leave_job = self.after(120, self._close_tip_if_outside)

    def _close_tip_if_outside(self):
        self._tip_leave_job = None
        widget = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        while widget is not None:
            if widget is self or widget is self.tip:
                return
            widget = widget.master
        self.hide_tip()

    def hide_tip(self):
        self.keep_tip()
        if self.tip_job is not None:
            self.after_cancel(self.tip_job)
            self.tip_job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def cleanup(self, event):
        if event.widget is self:
            self.hide_tip()

    def wheel(self, event):
        self.hide_tip()
        if self.on_wheel is not None:
            return self.on_wheel(event)
        # Embedded Text rows retain their old behavior; Frame parents are safe.
        parent = self.master
        while parent is not None:
            if hasattr(parent, 'yview_scroll'):
                if event.delta:
                    parent.yview_scroll(-1 if event.delta > 0 else 1, 'units')
                break
            parent = parent.master
        return 'break'
