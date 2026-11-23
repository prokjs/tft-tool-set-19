"""TFT-inspired trait bars. Badge colors indicate breakpoint progress, not Riot's
patch-specific rarity metadata (which is not included in the bundled snapshot).
"""
import re
import tkinter as tk
from tkinter import font as tkfont

from visuals import COST_COLORS
from localization import tr, display_text, localized_trait, localized_trait_description, localize_tree

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
        self._preserve_text = True
        self.trait = trait
        self.definition = definition
        self.artwork = artwork
        self.on_wheel = on_wheel
        self.members = members
        self.style_name = badge_style(trait, definition)
        self._fonts = {}
        self._height = None
        self.tip = None
        self.tip_canvas = None
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
        width = text_x + max(name_font.measure(display_text(localized_trait(self.trait['name']))), track_width) + 12
        return width, badge_right, divider, text_x, count, gap, self._fonts[height]

    def natural_width(self, height=None):
        """Exact content width: full name and full breakpoint track, never 306px."""
        return self._metrics(self._height if height is None else height)[0]

    def fitted_height(self, height):
        """Keep both text lines and the active underline inside the trait bar."""
        while True:
            fonts = self._metrics(height)[-1]
            required = fonts[0].metrics('linespace') + max(
                fonts[1].metrics('linespace'), fonts[2].metrics('linespace')) + 10
            if required <= height:
                return height
            height = required

    def set_height(self, height):
        """Redraw this same canvas in a compact or default size, retaining all data."""
        height = self.fitted_height(height)
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
        self.create_polygon(badge_right, 2, width - 12, 2, width - 3, 9,
                            width - 3, height - 2, badge_right, height - 2,
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
        name_y = 4 + name_font.metrics('linespace') / 2
        track_y = height - 4 - max(track_font.metrics('linespace'), current_font.metrics('linespace')) / 2
        self.create_text(text_x, name_y, text=display_text(localized_trait(self.trait['name'])), anchor='w',
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
                self.create_line(x, height - 2, x + point_width - 2, height - 2,
                                 fill=palette['edge'], width=2, tags='active-breakpoint')
            x += point_width

    def _effect_text(self, parent, description, background, wrap):
        """Keep sourced wording intact while separating effects and tier changes."""
        text = tk.Text(parent, wrap='word', height=1, width=1, bg=background,
                       fg='#e5e7eb', font=('Segoe UI', 10), relief='flat',
                       borderwidth=0, highlightthickness=0, takefocus=False,
                       cursor='arrow', padx=0, pady=2)
        text.pack(fill='x', padx=10)
        bold = ('Segoe UI', 10, 'bold')
        text.tag_configure('section', foreground='#c4b5fd', font=bold, spacing1=5, spacing3=4)
        text.tag_configure('current', foreground='#86efac', font=bold)
        text.tag_configure('muted', foreground='#a6b1c4')
        for tag, color in (('value', '#f1d69b'), ('ad', '#fdba74'), ('ap', '#d8b4fe'),
                           ('defense', '#67e8f9'), ('control', '#93c5fd')):
            text.tag_configure(tag, foreground=color, font=bold)
        text.insert('end', display_text(tr('WHAT IT DOES')) + '\n', 'section')
        has_breakpoints = False
        for line in description.splitlines():
            match = re.match(r'^(\d+)\s+(.+)$', line)
            if match and int(match[1]) in self.definition['breakpoints']:
                if not has_breakpoints:
                    text.insert('end', '\n' + display_text(tr('BREAKPOINTS')) + '\n', 'section')
                    has_breakpoints = True
                current = int(match[1]) == self.trait['tier']
                text.insert('end', f"({match[1]}) " + (display_text(tr('ACTIVE')) + ' · ' if current else ''),
                            'current' if current else 'muted')
                text.insert('end', display_text(match[2]) + '\n')
            else:
                text.insert('end', display_text(line) + '\n')
        wording = text.get('1.0', 'end-1c')
        highlights = {
            'value': r'\b\d+(?:\.\d+)?(?:%|x)?',
            'ad': ('Attack Damage',),
            'ap': ('Ability Power', 'magic damage'),
            'defense': ('Health', 'Shield', 'Durability', 'Armor', 'Magic Resist', 'Healing'),
            'control': ('Mana', 'Attack Speed', 'Stun', 'Freeze', 'inspirations'),
        }
        for tag, pattern in highlights.items():
            if isinstance(pattern, tuple):
                pattern = '|'.join(re.escape(display_text(tr(term))) for term in pattern)
            for match in re.finditer(pattern, wording, re.IGNORECASE):
                text.tag_add(tag, f'1.0+{match.start()}c', f'1.0+{match.end()}c')
        # Text display-line metrics are unavailable while its popup is withdrawn.
        # Measure wrapped text on the already-realized canvas instead; bold is a
        # conservative width for the mixed normal/bold body and tier headings.
        measure = self.create_text(-10000, -10000, text=wording, font=bold,
                                   width=max(1, wrap - 2), anchor='nw')
        bounds = self.bbox(measure)
        self.delete(measure)
        pixels = bounds[3] - bounds[1] + 24
        line_height = tkfont.Font(root=text, font=text.cget('font')).metrics('linespace')
        text.configure(height=max(1, (pixels + line_height - 1) // line_height), state='disabled')

    def queue_tip(self, event):
        self.show_tip(event.x_root, event.y_root)

    def show_tip(self, x=None, y=None):
        """Show sourced effects and the full supplied catalogue, using cached art."""
        self.hide_tip()
        if not self.winfo_ismapped():
            return
        if x is None:
            x = self.winfo_pointerx()
        if y is None:
            y = self.winfo_pointery()
        description = localized_trait_description(self.trait['name'], self.definition['description'])
        source = self.definition['description_source']
        self.tip = tk.Toplevel(self)
        tip = self.tip
        self.tip.withdraw()
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        self.tip.bind('<Enter>', lambda _event: self.hide_tip())
        background = '#253047'
        wrap = min(440, max(120, self.winfo_screenwidth() - 60))
        canvas = tk.Canvas(self.tip, bg=background, highlightthickness=0, width=wrap + 20, height=1)
        canvas.pack(side='left', fill='both', expand=True)
        self.tip_canvas = canvas
        content = tk.Frame(canvas, bg=background)
        canvas.create_window(0, 0, window=content, anchor='nw', width=wrap + 20)
        count = str(self.trait['count']) + ('★' if self.definition.get('count_mode') == 'stars' else '')
        tier = f"active breakpoint {self.trait['tier']}" if self.trait['tier'] else 'inactive'
        tk.Label(content, text=f"{self.trait['name']} · {count} · {tier}", justify='left',
                 wraplength=wrap, bg=background, fg='#f1d69b', padx=10, pady=7,
                 font=('Segoe UI', 12, 'bold')).pack(fill='x', anchor='w')
        tk.Label(content, text='Scroll on the trait for long effects · move off it to close',
                 bg=background, fg='#a6b1c4', padx=10,
                 font=('Segoe UI', 8)).pack(fill='x', anchor='w')
        self._effect_text(content, description, background, wrap)
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
        localize_tree(self.tip)
        tip.update_idletasks()
        if self.tip is not tip:
            return
        content_height = content.winfo_reqheight()
        height = min(content_height, max(80, self.winfo_screenheight() - 40))
        canvas.configure(height=height, scrollregion=(0, 0, wrap + 20, content_height))
        tip.update_idletasks()
        if self.tip is not tip:
            return
        width = self.tip.winfo_reqwidth()
        left, top = self.winfo_rootx(), self.winfo_rooty()
        right, bottom = left + self.winfo_width(), top + self.winfo_height()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        if right + width + 8 <= screen_width:
            x = right + 8
        elif left >= width + 8:
            x = left - width - 8
        else:
            x = max(0, min(left, screen_width - width))
            below, above = screen_height - bottom - 8, top - 8
            height = min(height, max(below, above))
            canvas.configure(height=max(1, height))
            y = bottom + 8 if below >= above else top - height - 8
        y = max(0, min(y, screen_height - height))
        self.tip.geometry(f'+{x}+{y}')
        self.tip.deiconify()

    @staticmethod
    def _scroll_tip(canvas, event):
        if event.delta and canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'


    def hide_tip(self):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        self.tip_canvas = None

    def leave_tip(self, _event=None):
        self.hide_tip()

    def cleanup(self, event):
        if event.widget is self:
            self.hide_tip()

    def wheel(self, event):
        if self.tip_canvas is not None and self.tip_canvas.yview() != (0.0, 1.0):
            return self._scroll_tip(self.tip_canvas, event)
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
