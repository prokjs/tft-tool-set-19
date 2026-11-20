"""TFT-inspired trait bars. Badge colors indicate breakpoint progress, not Riot's
patch-specific rarity metadata (which is not included in the bundled snapshot).
"""
import tkinter as tk
from tkinter import font as tkfont

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
    def __init__(self, parent, trait, definition, artwork):
        super().__init__(parent, width=306, height=44, bg='#192337', highlightthickness=0, takefocus=False)
        self.trait = trait
        self.definition = definition
        self.style_name = badge_style(trait, definition)
        palette = PALETTES[self.style_name]
        self.create_polygon(43, 5, 289, 5, 301, 14, 301, 39, 43, 39,
                            fill=palette['bar'], outline=palette['edge'], width=1)
        self.create_polygon(25, 1, 43, 11, 43, 33, 25, 43, 7, 33, 7, 11,
                            fill=palette['badge'], outline=palette['edge'], width=2)
        self.create_image(25, 22, image=artwork.trait_dark[trait['name']] if trait['tier'] else artwork.traits[trait['name']])
        self.create_line(80, 9, 80, 35, fill=palette['edge'])
        count = str(trait['count']) + ('★' if definition.get('count_mode') == 'stars' else '')
        self.create_text(62, 22, text=count, fill=palette['text'], font=('Segoe UI', 11, 'bold'), tags='count')
        self.create_text(90, 14, text=trait['name'], anchor='w', fill=palette['text'], font=('Segoe UI', 10, 'bold'))
        track_font = tkfont.Font(root=self, family='Segoe UI', size=9)
        x = 90
        for index, point in enumerate(definition['breakpoints']):
            if index:
                self.create_text(x, 31, text='›', anchor='w', fill='#7e8993', font=('Segoe UI', 9))
                x += 12
            current = point == trait['tier']
            completed = point <= trait['count'] and bool(trait['tier'])
            self.create_text(x, 31, text=str(point), anchor='w',
                             fill=palette['text'] if current else '#aaa695' if completed else '#7b8794',
                             font=('Segoe UI', 9, 'bold' if current else 'normal'), tags=f'breakpoint-{point}')
            width = track_font.measure(str(point)) + 3
            if current:
                self.create_line(x, 39, x + width - 2, 39, fill=palette['edge'], width=2, tags='active-breakpoint')
            x += width
