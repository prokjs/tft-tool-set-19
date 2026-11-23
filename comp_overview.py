"""Side-by-side selected-team overview using only bundled artwork and result data."""
import math
import tkinter as tk
from tkinter import ttk

from champion_info import trait_members, unlock_text
from board_view import BoardView, ChampionTip, champion_details, trait_support_label
from trait_rows import TraitRow, STYLE_ORDER, badge_style
from team_guidance import item_profile
from localization import tr, display_text, localize_tree
from visuals import COST_COLORS, Portrait


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
        stars = result.get('planned_stars', stars)
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
        tk.Label(team_header, text=f"TEAM · {len(result['champions'])} champions · frontline → backline", bg=PANEL,
                 fg=ACCENT, font=('Segoe UI', 9, 'bold'), anchor='w').pack(anchor='w')
        holders = result.get('item_holders', {})
        holder_summary = tk.Label(
            team_header, text='Item options (not inventory) · ' + ' · '.join(
                f"{kind.upper()}: " + (holders.get(kind) or (
                    'no dedicated holder' if kind in ('ad', 'ap') and holders.get('flex')
                    else 'no suitable holder'))
                for kind in ('tank', 'ad', 'ap', 'flex')),
            bg=PANEL, fg=ACCENT, font=('Segoe UI', 8), anchor='w', justify='left',
            wraplength=400)
        holder_summary.pack(fill='x')
        team_header.bind('<Configure>', lambda event:
                         holder_summary.configure(wraplength=max(100, event.width)))
        self.trait_panel, self.trait_canvas, self.trait_content, self.trait_scroll, self._trait_window = self._scroller(self.overview)
        self.trait_panel.grid(row=1, column=0, sticky='nsew', padx=(6, 8), pady=(0, 4))
        self.team_panel = tk.Frame(self.overview, bg=PANEL, width=1, height=1)
        self.team_panel.grid(row=1, column=1, sticky='nsew', padx=(4, 6), pady=(0, 4))
        self.team_content = tk.Frame(self.team_panel, bg=PANEL, width=1, height=108)
        self.team_content.place(x=0, y=0, relwidth=1, height=108)
        self.board = BoardView(self.team_panel, self.champions, self.artwork, self.on_unlock)
        self.board.place(x=0, y=112, relwidth=1, relheight=1, height=-112)
        self.board.show_comp(result, collab, locked, stars)
        self.card_tip = ChampionTip(self.team_content)
        ordered = sorted(result['traits'], key=lambda trait: (
            STYLE_ORDER[badge_style(trait, self.definitions[trait['name']])],
            -trait['count'], trait['name']))
        for trait in ordered:
            row = TraitRow(self.trait_content, trait, self.definitions[trait['name']], artwork=self.artwork,
                           height=32, on_wheel=lambda event: self._wheel(self.trait_canvas, event),
                           members=trait_members(self.data, trait['name'], collab, result.get('sona_band', '')))
            self.trait_rows[trait['name']] = row
        # Model order is presentation-only: preserve native/export champion order.
        selected = set(result['champions'])
        self._lineup_order = list(dict.fromkeys(
            name for name in result.get('lineup_order', result['champions']) if name in selected))
        self._lineup_order.extend(name for name in result['champions'] if name not in self._lineup_order)
        reasons = {marker['champion']: marker.get('reason', '')
                   for marker in result.get('positioning', [])}
        for name in self._lineup_order:
            champion = self.champions[name]
            color = COST_COLORS[champion['cost']]
            card = tk.Frame(self.team_content, bg=PANEL, highlightbackground=color,
                            highlightthickness=1, cursor='hand2', width=56, height=108)
            image = Portrait(card, self.artwork.champions[name], champion['cost'], stars.get(name, 1))
            label = tk.Label(card, text=name, bg=PANEL, fg=TEXT, font=('Segoe UI', 8, 'bold'),
                             anchor='center', justify='center', borderwidth=0)
            status = []
            if name in collab or name == 'Sona':
                status.append('C')
            if name in locked:
                status.append('L')
            if champion.get('slots', 1) > 1:
                status.append(f"{champion['slots']}s")
            badge = tk.Label(card, text=' · '.join(status), bg=PANEL, fg=color,
                             font=('Segoe UI', 7), borderwidth=0)
            holder_kinds = [kind.upper() for kind, holder in holders.items() if holder == name]
            action_text = 'Unlock details' if champion['unlockable'] else 'Unit details'
            if holder_kinds:
                action_text = ('AD/AP FLEX' if item_profile(champion) == 'flex'
                               else '/'.join(holder_kinds) + ' items')
            action = tk.Button(card, text=action_text,
                               command=lambda name=name: self.on_unlock(name), bg=PANEL,
                               fg=ACCENT if holder_kinds else WARNING if champion['unlockable'] else MUTED,
                               activebackground='#253047', activeforeground=TEXT, cursor='hand2',
                               font=('Segoe UI', 8, 'underline'), borderwidth=0, highlightthickness=0, pady=0, padx=0)
            support = result.get('champion_trait_support', {}).get(name)
            support_label = tk.Label(card, text=trait_support_label(support), bg=PANEL,
                                     fg=MUTED, font=('Segoe UI', 7), borderwidth=0)
            detail = champion_details(champion, stars.get(name, 1), ' · '.join(status),
                                      reasons.get(name, ''), holder_kinds, support)
            for widget in (card, image, label, badge, support_label):
                widget.bind('<Button-1>', lambda event, name=name: self.on_unlock(name))
                self.card_tip.bind(widget, detail)
            self.card_tip.bind(action, detail)
            self.champion_cards[name] = card
            self._card_parts[name] = (image, label, badge, action, support_label)
        self._build_unlocks(conditional)
        self.overview.bind('<Configure>', self._queue_layout)
        self.trait_canvas.bind('<Configure>', self._queue_layout)
        self.team_panel.bind('<Configure>', self._queue_layout)
        self._toggle_inactive()
        localize_tree(self)

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
        text.insert('end', display_text(tr('Requirements are conditions, not confirmed quest completion. Click a champion card for its sourced detail popup.\n')))
        if not names:
            text.insert('end', display_text(tr('\nNo selected champion has a special unlock requirement.')))
        for name in names:
            champion = self.champions[name]
            text.insert('end', display_text(tr(name)) + '\n', 'heading')
            text.insert('end', display_text(tr(unlock_text(champion))) + '\n')
            if champion.get('unlock_source'):
                text.insert('end', display_text(tr('Source')) + ': ' + champion['unlock_source'] + '\n')
        notes = self._result.get('strategy_notes', [])
        if notes:
            text.insert('end', display_text(tr('\nPLANNED TEAM ASSUMPTIONS\n')), 'heading')
            for note in notes:
                text.insert('end', '• ' + display_text(tr(note)) + '\n')
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
        height = max(1, self.team_panel.winfo_height())
        key = (width, height, self.team_panel.winfo_width(), tuple(self._visible_traits))
        if key == self._layout_key:
            return
        self._layout_key = key
        rows = [self.trait_rows[name] for name in self._visible_traits]
        row_height = 36 if len(rows) * 38 <= height else 32
        row_height = max((row.fitted_height(row_height) for row in rows), default=row_height)
        trait_width = max(190, max((row.natural_width(row_height) for row in rows), default=0))
        trait_height = len(rows) * (row_height + 2)
        scrolling = trait_height > height
        self.overview.grid_columnconfigure(0, minsize=trait_width + (18 if scrolling else 0) + 14)
        self.trait_content.grid_columnconfigure(0, minsize=trait_width)
        for index, row in enumerate(rows):
            row.set_height(row_height)
            row.grid(row=index, column=0, sticky='nw', pady=(0, 2))
        self.trait_canvas.itemconfigure(self._trait_window, width=trait_width, height=trait_height)
        self.trait_canvas.configure(scrollregion=(0, 0, trait_width, trait_height))
        if scrolling:
            self.trait_scroll.grid(row=0, column=1, sticky='ns')
        else:
            self.trait_scroll.grid_remove()
            self.trait_canvas.yview_moveto(0)
        # Place every card in the same physical row. No shared scroll or wrapping
        # can hide a carry below the frontline, even for eleven distinct units.
        team_width = max(1, self.team_panel.winfo_width())
        count = len(self.champion_cards)
        gap = 3 if team_width >= count * 48 else 2
        sizes = []
        heights = (0, 0, 0, 0)
        for index, name in enumerate(self._lineup_order):
            left = math.floor(index * team_width / max(1, count))
            right = math.floor((index + 1) * team_width / max(1, count))
            card_width = max(1, right - left - gap)
            image, label, badge, action, support_label = self._card_parts[name]
            label.configure(font=('Segoe UI', 7 if card_width < 64 else 8, 'bold'),
                            wraplength=max(1, card_width - 6), anchor='center', justify='center', padx=0, pady=0)
            action.configure(font=('Segoe UI', 7, 'underline'),
                             wraplength=max(1, card_width - 10), justify='center')
            for widget in (badge, support_label):
                widget.configure(wraplength=max(1, card_width - 6), padx=0, pady=0)
            measured = tuple(widget.winfo_reqheight() if widget.cget('text') else 0
                             for widget in (label, badge, support_label, action))
            heights = tuple(max(old, new) for old, new in zip(heights, measured))
            sizes.append((name, left, card_width))
        card_height = max(108, 50 + sum(heights))
        heights = (heights[0] + card_height - 50 - sum(heights), *heights[1:])
        self.team_content.place_configure(height=card_height)
        if self.team_panel.winfo_height() - card_height < 100:
            self.board.place_forget()
        else:
            self.board.place(x=0, y=card_height + 4, relwidth=1, relheight=1,
                             height=-(card_height + 4))
        for name, left, card_width in sizes:
            self._size_card(name, card_width, card_height, heights)
            self.champion_cards[name].place(x=left, y=0, width=card_width, height=card_height)

    def _size_card(self, name, width, height, heights):
        card = self.champion_cards[name]
        card.configure(width=width, height=height)
        image, label, badge, action, support_label = self._card_parts[name]
        image.place(x=(width - image.image.width()) // 2, y=3,
                    width=image.image.width(), height=image.image.height(), bordermode='outside')
        y = 38
        for widget, row_height in zip((label, badge, support_label, action), heights):
            if not row_height:
                widget.place_forget()
                continue
            widget.place(x=3, y=y, width=max(1, width - 6), height=row_height,
                         bordermode='outside')
            y += row_height + 3

    def _destroyed(self, event):
        if event.widget is self and self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
