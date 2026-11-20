"""Side-by-side selected-team overview using only bundled artwork and result data."""
import math
import tkinter as tk
from tkinter import ttk

from champion_info import star_text, trait_members, unlock_text
from trait_rows import TraitRow, STYLE_ORDER, badge_style
from visuals import COST_COLORS


PANEL = '#192337'
TEXT = '#e5e7eb'
MUTED = '#9ca3af'
ACCENT = '#c4b5fd'
WARNING = '#f1d69b'


class CompOverview(tk.Frame):
    """Render a selected result without putting its traits above its lineup.

    ``show_comp(result, collab, locked, stars)`` replaces the current team;
    ``show_message(text)`` replaces it with actual empty-state instructions.
    Cards open ``on_unlock(name)``. Inactive traits are explicitly opt-in and
    complete sourced requirements remain available on the Unlocks tab.
    """

    def __init__(self, parent, data, artwork, on_unlock):
        super().__init__(parent, bg=PANEL, width=640, height=330)
        self.data = data
        self.champions = {champion['name']: champion for champion in data['champions']}
        self.definitions = {trait['name']: trait for trait in data['traits']}
        self.artwork = artwork
        self.on_unlock = on_unlock
        self.champion_cards = {}
        self.trait_rows = {}
        self.show_inactive = tk.BooleanVar(master=self, value=False)
        self._card_parts = {}
        self._result = None
        self._layout_job = None
        self._layout_key = None
        self._visible_traits = []
        self.show_inactive.trace_add('write', self._inactive_changed)
        self.bind('<Destroy>', self._destroyed, add='+')
        self.show_message('Select a suggested team to view its traits and champions.')

    def _clear(self):
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        for child in self.winfo_children():
            child.destroy()
        self.champion_cards.clear()
        self.trait_rows.clear()
        self._card_parts.clear()
        self._visible_traits = []
        self._layout_key = None

    def show_message(self, text):
        self._clear()
        self._result = None
        message = tk.Message(self, text=text, bg=PANEL, fg=TEXT,
                             font=('Segoe UI', 11), anchor='nw', padx=14, pady=12)
        message.pack(fill='both', expand=True)
        message.bind('<Configure>', lambda event: message.configure(width=max(80, event.width - 28)))

    def show_comp(self, result, collab, locked, stars):
        self._clear()
        self._result = result
        self.show_inactive.set(False)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        self.overview = tk.Frame(self.tabs, bg=PANEL)
        self.tabs.add(self.overview, text='Overview')
        self.unlocks = tk.Frame(self.tabs, bg=PANEL)
        conditional = [name for name in result['champions'] if self.champions[name]['unlockable']]
        self.tabs.add(self.unlocks, text=f'Unlocks ({len(conditional)})')
        self.overview.grid_rowconfigure(1, weight=1)
        self.overview.grid_columnconfigure(1, weight=1)
        self.trait_header = tk.Frame(self.overview, bg=PANEL)
        self.trait_header.grid(row=0, column=0, sticky='ew', padx=(6, 8), pady=(4, 2))
        unique = sum(bool(trait['tier']) and trait['unique'] for trait in result['traits'])
        self.trait_title = tk.Label(self.trait_header,
                                   text=f"TRAITS · {result['active']} active + {unique} unique",
                                   bg=PANEL, fg=ACCENT, font=('Segoe UI', 9, 'bold'), anchor='w')
        self.trait_title.pack(anchor='w')
        inactive = sum(not trait['tier'] for trait in result['traits'])
        tk.Checkbutton(self.trait_header, text=f'Show inactive ({inactive})', variable=self.show_inactive,
                       bg=PANEL, fg=MUTED, selectcolor=PANEL,
                       activebackground=PANEL, activeforeground=TEXT, font=('Segoe UI', 8),
                       borderwidth=0, highlightthickness=0, padx=0, pady=0).pack(anchor='w')
        team_header = tk.Frame(self.overview, bg=PANEL)
        self.team_header = team_header
        team_header.grid(row=0, column=1, sticky='ew', padx=(4, 6), pady=(4, 2))
        tk.Label(team_header, text=f"TEAM · {len(result['champions'])} champions", bg=PANEL,
                 fg=ACCENT, font=('Segoe UI', 9, 'bold'), anchor='w').pack(anchor='w')
        tk.Label(team_header, text='C = Collab · L = locked · click for details', bg=PANEL,
                 fg=MUTED, font=('Segoe UI', 8), anchor='w').pack(anchor='w')
        self.trait_panel, self.trait_canvas, self.trait_content, self.trait_scroll, self._trait_window = self._scroller(self.overview)
        self.trait_panel.grid(row=1, column=0, sticky='nsew', padx=(6, 8), pady=(0, 4))
        self.team_panel, self.team_canvas, self.team_content, self.team_scroll, self._team_window = self._scroller(self.overview)
        self.team_panel.grid(row=1, column=1, sticky='nsew', padx=(4, 6), pady=(0, 4))
        self.layout_note = tk.Label(self.overview, bg=PANEL, fg=WARNING, font=('Segoe UI', 8), anchor='w')
        self.layout_note.grid(row=2, column=0, columnspan=2, sticky='ew', padx=6)
        self.layout_note.grid_remove()
        ordered = sorted(result['traits'], key=lambda trait: (
            STYLE_ORDER[badge_style(trait, self.definitions[trait['name']])],
            -trait['count'], trait['name']))
        for trait in ordered:
            row = TraitRow(self.trait_content, trait, self.definitions[trait['name']], artwork=self.artwork,
                           height=32, on_wheel=lambda event: self._wheel(self.trait_canvas, event),
                           members=trait_members(self.data, trait['name'], collab, result.get('sona_band', '')))
            self.trait_rows[trait['name']] = row
        for name in result['champions']:
            champion = self.champions[name]
            color = COST_COLORS[champion['cost']]
            card = tk.Frame(self.team_content, bg=PANEL, highlightbackground=color,
                            highlightthickness=1, cursor='hand2', width=126, height=134)
            image = tk.Label(card, image=self.artwork.portraits[name], bg=PANEL, borderwidth=0)
            label = tk.Label(card, text=name, bg=PANEL, fg=TEXT, font=('Segoe UI', 9, 'bold'),
                             anchor='center', justify='center', borderwidth=0)
            badges = f"{champion['cost']}g · {star_text(stars.get(name, 1))}"
            if name in collab or name == 'Sona':
                badges += ' · C'
            if name in locked:
                badges += ' · L'
            if champion.get('slots', 1) > 1:
                badges += f" · {champion['slots']}s"
            badge = tk.Label(card, text=badges, bg=PANEL, fg=color, font=('Segoe UI', 8), borderwidth=0)
            action = tk.Button(card, text='Unlock details' if champion['unlockable'] else 'Unit details',
                               command=lambda name=name: self.on_unlock(name), bg=PANEL,
                               fg=WARNING if champion['unlockable'] else MUTED,
                               activebackground='#253047', activeforeground=TEXT, cursor='hand2',
                               font=('Segoe UI', 8, 'underline'), borderwidth=0, highlightthickness=0, pady=0, padx=0)
            for widget in (card, image, label, badge):
                widget.bind('<Button-1>', lambda event, name=name: self.on_unlock(name))
                widget.bind('<MouseWheel>', lambda event: self._wheel(self.team_canvas, event))
            action.bind('<MouseWheel>', lambda event: self._wheel(self.team_canvas, event))
            self.champion_cards[name] = card
            self._card_parts[name] = (image, label, badge, action)
        self._build_unlocks(conditional)
        self.overview.bind('<Configure>', self._queue_layout)
        self.trait_canvas.bind('<Configure>', self._queue_layout)
        self.team_canvas.bind('<Configure>', self._queue_layout)
        self._toggle_inactive()

    def _scroller(self, parent):
        panel = tk.Frame(parent, bg=PANEL, width=1, height=1)
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        canvas = tk.Canvas(panel, bg=PANEL, highlightthickness=0, width=1, height=1)
        canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(canvas, bg=PANEL)
        window = canvas.create_window(0, 0, window=content, anchor='nw')
        canvas.bind('<MouseWheel>', lambda event: self._wheel(canvas, event))
        content.bind('<MouseWheel>', lambda event: self._wheel(canvas, event))
        return panel, canvas, content, scrollbar, window

    @staticmethod
    def _wheel(canvas, event):
        if event.delta and canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _build_unlocks(self, names):
        text = tk.Text(self.unlocks, wrap='word', bg=PANEL, fg=TEXT, relief='flat',
                       font=('Segoe UI', 10), padx=12, pady=8, width=1, height=1)
        scroll = ttk.Scrollbar(self.unlocks, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.tag_configure('heading', foreground=WARNING, font=('Segoe UI', 10, 'bold'), spacing1=10)
        text.insert('end', 'Requirements are conditions, not confirmed quest completion. Click a champion card for its sourced detail popup.\n')
        if not names:
            text.insert('end', '\nNo selected champion has a special unlock requirement.')
        for name in names:
            champion = self.champions[name]
            text.insert('end', name + '\n', 'heading')
            text.insert('end', unlock_text(champion) + '\n')
            if champion.get('unlock_source'):
                text.insert('end', 'Source: ' + champion['unlock_source'] + '\n')
        notes = self._result.get('strategy_notes', [])
        if notes:
            text.insert('end', '\nPLANNED TEAM ASSUMPTIONS\n', 'heading')
            for note in notes:
                text.insert('end', '• ' + note + '\n')
        text.configure(state='disabled')

    def _inactive_changed(self, *_args):
        if self._result is not None and self.trait_rows:
            self._toggle_inactive()

    def _toggle_inactive(self):
        self._visible_traits = [name for name, row in self.trait_rows.items()
                                if row.trait['tier'] or self.show_inactive.get()]
        for row in self.trait_rows.values():
            row.grid_forget()
        self._layout_key = None
        self.trait_canvas.yview_moveto(0)
        self._queue_layout()

    def _queue_layout(self, _event=None):
        if self._result is not None and self._layout_job is None:
            self._layout_job = self.after_idle(self._reflow)

    def _reflow(self):
        self._layout_job = None
        if self._result is None:
            return
        width = max(1, self.overview.winfo_width() - 24)
        header_height = max(self.trait_header.winfo_reqheight(), self.team_header.winfo_reqheight())
        note_height = self.layout_note.winfo_reqheight() if self.layout_note.winfo_manager() else 0
        height = max(1, self.overview.winfo_height() - header_height - note_height - 10)
        key = (width, height, tuple(self._visible_traits))
        if key == self._layout_key:
            return
        self._layout_key = key
        rows = [self.trait_rows[name] for name in self._visible_traits]
        count = len(self.champion_cards)
        candidates = []
        # Prefer readable large cards, then smaller cards/trait rows, then columns.
        # A candidate must fit BOTH panels; the lineup never flows below traits.
        for row_height in (44, 36, 32):
            widths = [row.natural_width(row_height) for row in rows]
            for trait_columns in range(1, min(3, len(rows)) + 1):
                per_column = max(1, math.ceil(len(rows) / trait_columns))
                column_widths = [max(widths[column * per_column:(column + 1) * per_column], default=0)
                                 for column in range(trait_columns)]
                trait_width = max(190, sum(column_widths) + 4 * max(0, trait_columns - 1))
                trait_height = per_column * (row_height + 2)
                for card_width, card_height, large in ((126, 134, True), (126, 84, False), (120, 68, False), (112, 68, False)):
                    team_width = width - trait_width
                    team_columns = max(1, team_width // (card_width + 4))
                    team_height = math.ceil(count / team_columns) * (card_height + 4)
                    overflow = max(0, trait_height - height) + max(0, team_height - height) + max(0, card_width + 4 - team_width) * 4
                    quality = (int(large) * 100 + row_height - trait_columns * 4 + card_height / 10 + card_width / 100)
                    candidates.append((overflow, -quality, trait_width, trait_columns, per_column,
                                       column_widths, row_height, card_width, card_height, team_columns, large))
        if not candidates:
            # Legal results can contain no counted traits.
            candidates.append((0, 0, 190, 1, 1, [190], 32, 112, 68,
                               max(1, (width - 190) // 116), False))
        choice = min(candidates, key=lambda candidate: candidate[:2])
        _, _, trait_width, trait_columns, per_column, column_widths, row_height, card_width, card_height, team_columns, large = choice
        self.overview.grid_columnconfigure(0, minsize=trait_width + 14)
        for column in range(3):
            self.trait_content.grid_columnconfigure(column, minsize=column_widths[column] + 4 if column < len(column_widths) else 0)
        for index, row in enumerate(rows):
            row.set_height(row_height)
            row.grid(row=index % per_column, column=index // per_column, sticky='nw', pady=(0, 2))
        for index, (name, card) in enumerate(self.champion_cards.items()):
            self._size_card(name, card_width, card_height, large)
            card.grid(row=index // team_columns, column=index % team_columns, sticky='nw', padx=(0, 4), pady=(0, 4))
        trait_height = per_column * (row_height + 2) if rows else 0
        team_height = math.ceil(count / team_columns) * (card_height + 4)
        warnings = []
        for canvas, scroll, window, content_width, content_height, label in (
                (self.trait_canvas, self.trait_scroll, self._trait_window, trait_width, trait_height, 'traits'),
                (self.team_canvas, self.team_scroll, self._team_window,
                 team_columns * (card_width + 4), team_height, 'lineup')):
            canvas.itemconfigure(window, width=content_width, height=content_height)
            canvas.configure(scrollregion=(0, 0, content_width, content_height))
            if content_height > height:
                scroll.grid(row=0, column=1, sticky='ns')
                warnings.append(label)
            else:
                scroll.grid_remove()
                canvas.yview_moveto(0)
        if warnings:
            self.layout_note.configure(text='Independent scroll: ' + ' / '.join(warnings) + '. Enlarge the result area to show all rows.')
            self.layout_note.grid()
        else:
            self.layout_note.configure(text='')
            self.layout_note.grid_remove()

    def _size_card(self, name, width, height, large):
        card = self.champion_cards[name]
        card.configure(width=width, height=height)
        image, label, badge, action = self._card_parts[name]
        label.configure(font=('Segoe UI', 8 if height == 68 else 9, 'bold'))
        if large:
            image.configure(image=self.artwork.portraits[name])
            image.place(x=(width - 56) // 2, y=4, width=56, height=56)
            label.configure(wraplength=width - 8, anchor='center', justify='center')
            label.place(x=4, y=62, width=width - 8, height=34)
            badge.place(x=2, y=98, width=width - 4, height=16)
            action.place(x=2, y=115, width=width - 4, height=16)
        else:
            image.configure(image=self.artwork.champions[name])
            image.place(x=4, y=3 if height == 68 else 6, width=32, height=32)
            label.configure(wraplength=width - 44, anchor='w', justify='left')
            label.place(x=40, y=3, width=width - 44, height=30 if height == 68 else 42)
            badge.place(x=2, y=35 if height == 68 else 46, width=width - 4, height=15)
            action.place(x=2, y=height - 19, width=width - 4, height=16)

    def _destroyed(self, event):
        if event.widget is self and self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
