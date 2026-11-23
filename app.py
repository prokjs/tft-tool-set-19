"""Offline Collab optimizer: load five or six disjoint-trait champions + Sona.

The same grid filters Collab candidates while the pool is unlocked, then restores
the full roster for team-building once the pool is locked.
"""
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import webbrowser

from optimizer import load_data, BAND_TRAITS, planned_stars
from visuals import Artwork
from pool_picker import POOL_SIZES, eligible_champions, visible_champions
from champion_grid import ChampionGrid
from comp_overview import CompOverview
from champion_info import unlock_text, star_text
from buildup import recommend_with_buildups, replan_buildup, format_buildup
from buildup_view import BuildupView
from team_code import encode_team
from app_settings import settings_path, load_language, save_language
from localization import available_languages, set_language, get_language, tr, display_text, localize_tree
from legal_notice import (RIOT_NOTICE, PROTOTYPE_NOTICE, PURPOSE_NOTICE, LIABILITY_NOTICE,
                          CONTENT_NOTICE, POLICY_LINKS, PUBLIC_RELEASE_REQUIREMENTS)
from help_view import TutorialWindow
from app_version import APP_NAME, APP_VERSION, APP_TITLE


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('1280x850')
        self.minsize(1060, 720)
        if sys.platform == 'win32':
            self.state('zoomed')
        self.configure(bg='#111827')
        self.bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
        self.language_path = settings_path()
        language, language_error = load_language(self.language_path, dict(available_languages()))
        set_language(language)
        self.data = load_data(self.bundle / 'data' / 'set19.json')
        self.artwork = Artwork(self, self.bundle / 'assets')
        self.artwork.mark_unlockables(self.data['champions'])
        self.champions = {c['name']: c for c in self.data['champions']}
        self.collab, self.locked, self.excluded = set(), set(), set()
        self.pool_locked = False
        self.stars = {}
        self.results = []
        self.messages = queue.Queue()
        self.busy = False
        self.active_result_index = None
        self.size = tk.StringVar(value='10')
        self.minimum = tk.StringVar(value='3')
        self.mode = tk.StringVar(value='High value')
        self.band = tk.StringVar(value='None')
        self.search = tk.StringVar()
        self.status = tk.StringVar(value='Pick your Collab champions in the portrait grid, then lock the pool.')
        self._style()
        self._layout()
        self.render_roster()
        self.invalidate()
        if language_error:
            self.status.set('Language settings could not be read; using English. ' + language_error)
        localize_tree(self)
        self.after(100, self.drain)
        self.size.trace_add('write', self.change_level)
        for variable in (self.minimum, self.mode):
            variable.trace_add('write', lambda *_: self.invalidate())
        self.band.trace_add('write', self.change_level)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background='#111827', foreground='#e5e7eb', font=('Segoe UI', 10))
        style.configure('TFrame', background='#111827')
        style.configure('TLabel', background='#111827')
        style.configure('Muted.TLabel', foreground='#9ca3af')
        style.configure('Title.TLabel', font=('Segoe UI', 17, 'bold'), foreground='#c4b5fd')
        style.configure('TButton', padding=(10, 7), background='#303d56')
        style.map('TButton', background=[('active', '#4c3b78')])
        style.configure('Accent.TButton', background='#6d28d9', font=('Segoe UI', 10, 'bold'))
        style.configure('Treeview', background='#192337', fieldbackground='#192337', foreground='#e5e7eb', rowheight=29)
        style.configure('Roster.Treeview', rowheight=38)
        style.configure('Treeview.Heading', background='#29354c', padding=6)
        style.map('Treeview', background=[('selected', '#5b3c91')])
        style.configure('TCombobox', fieldbackground='#253047', foreground='#ffffff', arrowcolor='#ffffff')
        style.map('TCombobox', fieldbackground=[('readonly', '#253047')], foreground=[('readonly', '#ffffff')])
        style.configure('TEntry', fieldbackground='#253047', foreground='#ffffff')
        style.configure('Slim.Horizontal.TProgressbar', thickness=3)

    def _layout(self):
        root = ttk.Frame(self, padding=(20, 14))
        root.pack(fill='both', expand=True)
        header = ttk.Frame(root)
        header.pack(fill='x', pady=(0, 8))
        header.columnconfigure(1, weight=1)
        brand = ttk.Frame(header)
        brand.grid(row=0, column=0, sticky='w')
        brand_line = ttk.Frame(brand)
        brand_line.pack(anchor='w')
        brand_title = ttk.Label(brand_line, text=APP_NAME, style='Title.TLabel')
        brand_title._preserve_text = True
        brand_title.pack(side='left')
        self.version_label = ttk.Label(brand_line, text=f'v{APP_VERSION}', foreground='#c4b5fd',
                                       font=('Segoe UI', 10, 'bold'))
        self.version_label._preserve_text = True
        self.version_label.pack(side='left', padx=(8, 0))
        ttk.Label(brand, text='Developed by prokjs · Offline team explorer', style='Muted.TLabel').pack(anchor='w')
        controls = ttk.Frame(header)
        controls.grid(row=0, column=1, sticky='e', padx=(14, 0))
        settings = ttk.Frame(header)
        settings.grid(row=0, column=2, sticky='ne', padx=(8, 0))
        self.language_button = ttk.Button(settings, text='Language settings', command=self.language_settings)
        self.language_button.pack(anchor='e')
        self.tutorial_button = ttk.Button(settings, text='How to use', command=lambda: TutorialWindow(self))
        self.tutorial_button.pack(anchor='e', pady=(3, 0))
        for col, (label, variable, values, width) in enumerate([
            ('Final level', self.size, list(map(str, range(3, 12))), 8),
            ('Minimum Collab', self.minimum, ['0', '3', '5'], 14),
            ('Ranking', self.mode, ['High value', 'Balanced', 'Most active traits'], 20),
            ('Sona featured band', self.band, ['None'] + sorted(BAND_TRAITS), 19),
        ]):
            box = ttk.Frame(controls)
            box.grid(row=0, column=col, padx=(0, 14), sticky='w')
            ttk.Label(box, text=label).pack(anchor='w', pady=(0, 4))
            ttk.Combobox(box, textvariable=variable, values=values, state='readonly', width=width).pack()
        self.run_button = ttk.Button(controls, text='Find teams', style='Accent.TButton', command=self.run)
        self.run_button.grid(row=0, column=4, sticky='s', padx=8)
        def arrange_header(event):
            wrapped = event.width < brand.winfo_reqwidth() + controls.winfo_reqwidth() + self.language_button.winfo_reqwidth() + 22
            row = 1 if wrapped else 0
            if int(controls.grid_info()['row']) != row:
                controls.grid(row=row, column=0 if wrapped else 1,
                              columnspan=3 if wrapped else 1, sticky='w' if wrapped else 'e',
                              padx=0 if wrapped else (14, 0), pady=(8, 0) if wrapped else 0)
        header.bind('<Configure>', arrange_header)
        self.progress = ttk.Progressbar(root, mode='indeterminate', style='Slim.Horizontal.TProgressbar')
        ttk.Label(root, textvariable=self.status, wraplength=1150).pack(side='bottom', anchor='w', pady=(10, 0))
        self.riot_notice = ttk.Label(root, text=RIOT_NOTICE, wraplength=1150,
                                     style='Muted.TLabel', font=('Segoe UI', 8))
        self.riot_notice._preserve_text = True
        self.riot_notice.pack(side='bottom', fill='x', pady=(4, 0))
        self.purpose_notice = ttk.Label(root, text='Combinations only · Not intended to improve winning chances · No outcomes guaranteed · Use at own risk · Unapproved prototype.',
                                       foreground='#f1d69b', font=('Segoe UI', 8, 'bold'), wraplength=1150)
        self.purpose_notice.pack(side='bottom', fill='x', pady=(3, 0))
        root.bind('<Configure>', lambda event: (
            self.riot_notice.configure(wraplength=max(320, event.width - 4)),
            self.purpose_notice.configure(wraplength=max(320, event.width - 4))))
        panes = ttk.Panedwindow(root, orient='horizontal')
        self._panes = panes
        panes.pack(fill='both', expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 14, 0))
        right = ttk.Frame(panes)
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        pane_width = 0
        def arrange_panes(event):
            nonlocal pane_width
            if event.width != pane_width:
                pane_width = event.width
                panes.sashpos(0, min(560, max(360, int(event.width * 0.43))))
        panes.bind('<Configure>', arrange_panes)
        pool_heading = ttk.Frame(left)
        pool_heading.pack(fill='x', pady=(0, 4))
        ttk.Label(pool_heading, text='THEORYCRAFT POOL', font=('Segoe UI', 12, 'bold')).pack(side='left')
        self.pool_button = ttk.Button(pool_heading, text='Lock Collab pool', command=self.toggle_pool_lock)
        self.pool_button.pack(side='right')
        self.pool_hint = ttk.Label(left, wraplength=520, foreground='#f1d69b')
        self.pool_hint.pack(fill='x', pady=(0, 2))
        self.pool_strip = tk.Text(left, height=2, width=1, bg='#111827', relief='flat', state='disabled', cursor='arrow')
        self.pool_strip.pack(fill='x', pady=(0, 4))
        ttk.Label(left, text='Search champions or traits', style='Muted.TLabel').pack(anchor='w')
        entry = ttk.Entry(left, textvariable=self.search)
        entry.pack(fill='x', pady=(0, 6))
        self.search.trace_add('write', lambda *_: self.champion_grid.set_search(self.search.get()))
        roster_frame = ttk.Frame(left)
        roster_frame.pack(fill='both', expand=True)
        self.champion_grid = ChampionGrid(roster_frame, self.artwork, self.show_champion, self.activate_champion)
        self.champion_grid.pack(fill='both', expand=True)
        buttons = ttk.Frame(left)
        buttons.pack(fill='x', pady=7)
        self.team_controls = []
        for text, action in [('Team lock', 'locked'), ('Exclude', 'excluded')]:
            button = ttk.Button(buttons, text=text, command=lambda a=action: self.toggle(a))
            button.pack(side='left', padx=(0, 5))
            self.team_controls.append(button)
        star_row = ttk.Frame(left)
        star_row.pack(fill='x')
        ttk.Label(star_row, text='Planned stars:').pack(side='left')
        for star in range(1, 5):
            button = ttk.Button(star_row, text=star_text(star), width=0, padding=(3, 5), command=lambda s=star: self.set_stars(s))
            button.pack(side='left', padx=2)
            self.team_controls.append(button)
        ttk.Button(star_row, text='Reset', command=self.reset).pack(side='right')
        self.champion_label = ttk.Label(left, width=1, anchor='w', style='Muted.TLabel')
        self.champion_label.pack(fill='x', pady=(3, 0))
        full_roster_help = ('C = Collab   L = team locked   X = excluded\n'
                            'Pool unlocked: click to add/remove picks. Sona is automatic.\n'
                            'Pool locked: Ctrl / Shift multi-select for team controls.\n'
                            'Unlock tasks still apply; hover/select to see requirements.\n'
                            'K/DA uses stars; Baron Nashor occupies two slots.\n'
                            'Arrow keys navigate; Space toggles selection; Enter activates.\n'
                            'Search dims nonmatches without moving champions.')
        roster_help = ttk.Label(left, text='C = Collab   L = team locked   X = excluded · How to use',
                                style='Muted.TLabel', wraplength=520, cursor='question_arrow', takefocus=True)
        roster_help.pack(anchor='w')
        roster_help.bind('<Enter>', lambda event: self.champion_grid.show_help(event, full_roster_help))
        roster_help.bind('<FocusIn>', lambda event: self.champion_grid.show_help(event, full_roster_help))
        roster_help.bind('<FocusOut>', lambda _: self.champion_grid.hide_tip())
        roster_help.bind('<Button-1>', lambda event: self.champion_grid.show_help(event, full_roster_help))
        roster_help.bind('<Leave>', lambda _: self.champion_grid.hide_tip())
        def wrap_roster_details(event):
            self.pool_hint.configure(wraplength=max(180, event.width - 28))
            roster_help.configure(wraplength=max(180, event.width - 28))
        left.bind('<Configure>', wrap_roster_details)
        ttk.Label(right, text='SUGGESTED TEAMS', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        result_frame = ttk.Frame(right)
        result_frame.pack(fill='x', pady=6)
        self.result_list = ttk.Treeview(result_frame, columns=('rank', 'active', 'tiers', 'front', 'cost'), show='headings', selectmode='browse', height=2)
        for key, title, width in [('rank', 'Rank', 50), ('active', 'Active traits', 100), ('tiers', 'Trait tiers', 90), ('front', 'Frontline', 85), ('cost', 'Gold', 65)]:
            self.result_list.heading(key, text=title)
            self.result_list.column(key, width=width, stretch=True)
        result_scroll = ttk.Scrollbar(result_frame, orient='vertical', command=self.result_list.yview)
        self.result_list.configure(yscrollcommand=result_scroll.set)
        self.result_list.pack(side='left', fill='x', expand=True)
        result_scroll.pack(side='right', fill='y')
        self.result_list.bind('<<TreeviewSelect>>', self.show_result)
        result_panes = ttk.Panedwindow(right, orient='vertical')
        final_panel = ttk.Frame(result_panes)
        final_heading = ttk.Frame(final_panel)
        final_heading.pack(fill='x')
        ttk.Label(final_heading, text='SELECTED END GOAL', foreground='#c4b5fd',
                  font=('Segoe UI', 10, 'bold')).pack(side='left', pady=(2, 4))
        self.expand_final_board = ttk.Button(
            final_heading, text='Expand final board',
            command=lambda: result_panes.sashpos(0, max(260, result_panes.winfo_height() - 180)))
        self.expand_final_board.pack(side='right')
        self.overview = CompOverview(final_panel, self.data, self.artwork, self.show_unlock)
        self.overview.pack(fill='both', expand=True)
        self.buildup = BuildupView(result_panes, self.data, self.artwork, self.show_unlock,
                                   self.choose_temporary)
        result_panes.add(final_panel, weight=2)
        result_panes.add(self.buildup, weight=3)
        result_height = 0
        def arrange_results(event):
            nonlocal result_height
            if event.height > 1 and event.height != result_height:
                ratio = result_panes.sashpos(0) / result_height if result_height else 0.45
                result_height = event.height
                result_panes.sashpos(0, min(max(180, event.height - 180),
                                          max(260, int(event.height * max(0.35, min(0.70, ratio))))))
        result_panes.bind('<Configure>', arrange_results)
        disclaimer = ttk.Label(right, text='Heuristic combinations and positions — not combat strength or winning odds.', style='Muted.TLabel', wraplength=610)
        disclaimer.pack(side='bottom', fill='x')
        right.bind('<Configure>', lambda event: disclaimer.configure(wraplength=max(240, event.width - 8)))
        actions = ttk.Frame(right)
        actions.pack(side='bottom', fill='x', pady=8)
        ttk.Button(actions, text='Copy TFT code', command=self.copy_tft_code).pack(side='left')
        ttk.Button(actions, text='Copy plan', command=self.copy_team).pack(side='left', padx=6)
        ttk.Button(actions, text='Save JSON', command=self.export).pack(side='left')
        ttk.Button(actions, text='Data and legal', command=self.sources).pack(side='right')
        result_panes.pack(fill='both', expand=True)

    def activate_champion(self, name):
        """Consume portrait activation only while editing the Collab pool."""
        if self.pool_locked:
            return False
        if self.busy:
            return True
        if name == 'Sona':
            self.status.set('Sona is automatic and does not count toward your 5 or 6 pool picks.')
            return True
        if name in self.collab:
            self.collab.remove(name)
        elif name in eligible_champions(self.champions, self.collab):
            self.collab.add(name)
        else:
            self.status.set('That champion conflicts with the current pool or the six-pick limit.')
            return True
        self.render_roster()
        self.invalidate()
        return True

    def toggle_pool_lock(self):
        if self.busy:
            return
        if not self.pool_locked and len(self.collab) not in POOL_SIZES:
            self.status.set('Pick 5 or 6 Collab champions before locking the pool.')
            return
        self.pool_locked = not self.pool_locked
        self.champion_grid.clear_selection()
        if self.search.get():
            self.search.set('')
        self.render_roster()
        self.invalidate()

    def sync_controls(self):
        valid_pool = len(self.collab) in POOL_SIZES
        self.pool_button.configure(text='Unlock Collab pool' if self.pool_locked else 'Lock Collab pool')
        self.pool_button.state(['disabled'] if self.busy or (not self.pool_locked and not valid_pool) else ['!disabled'])
        for button in self.team_controls:
            button.state(['disabled'] if self.busy or not self.pool_locked else ['!disabled'])
        self.run_button.state(['!disabled'] if self.pool_locked and valid_pool and not self.busy else ['disabled'])
        self.buildup.set_busy(self.busy)

    def invalidate(self):
        self.results = []
        self.result_list.delete(*self.result_list.get_children())
        self.active_result_index = None
        self.buildup.show_message('Select a suggested team to see every level of its guide here.')
        self.overview.show_message('Use the main portrait grid to pick your Collab pool.\n\nWhile unlocked, click to add/remove champions. Conflicting candidates disappear; selected picks remain removable. Sona is automatic.\n\nLock a 5- or 6-champion pool to restore the full roster and enable team locks, exclusions, star controls, and recommendations. Unlock the pool to edit it again.')
        self.sync_controls()
        self.status.set('Pool locked. Select team units or find recommendations.' if self.pool_locked else f'Pool unlocked: {len(self.collab)} picked. Choose 5 or 6, then lock the pool.')

    def change_level(self, *_):
        self.render_roster()
        self.invalidate()

    def render_roster(self):
        entries = []
        visible = visible_champions(self.champions, self.collab, self.pool_locked)
        effective_stars = planned_stars(self.champions, int(self.size.get()), self.stars,
                                        '' if self.band.get() == 'None' else self.band.get())
        for c in sorted(self.champions.values(), key=lambda c: (c['cost'], c['name'])):
            name = c['name']
            if name not in visible:
                continue
            flags = 'C ' if name in self.collab or name == 'Sona' else ''
            if self.pool_locked:
                flags += ('L ' if name in self.locked else '') + ('X' if name in self.excluded else '')
            entries.append({**c, 'flags': flags.strip(), 'stars': effective_stars[name],
                            'pool_member': not self.pool_locked and name in self.collab,
                            'fixed': not self.pool_locked and name == 'Sona'})
        self.champion_grid.set_entries(entries)
        self.champion_grid.set_search(self.search.get())
        self.pool_hint.configure(text=(f'LOCKED · {len(self.collab)} picks + Sona. Full roster.' if self.pool_locked else f'UNLOCKED · {len(self.collab)}/{max(POOL_SIZES)} picks + Sona. Click to pick.'))
        self.pool_strip.configure(state='normal')
        self.pool_strip.delete('1.0', 'end')
        for name in sorted(self.collab) + ['Sona']:
            self.pool_strip.image_create('end', image=self.artwork.champions[name], padx=3, pady=3)
        self.pool_strip.configure(state='disabled')
        self.sync_controls()
        localize_tree(self)

    def toggle(self, target):
        if self.busy or not self.pool_locked:
            return
        collection = getattr(self, target)
        for name in self.champion_grid.selected_names():
            if name in collection:
                collection.remove(name)
            else:
                collection.add(name)
                if target == 'locked':
                    self.excluded.discard(name)
                if target == 'excluded':
                    self.locked.discard(name)
        self.render_roster()
        self.invalidate()

    def set_stars(self, value):
        if not self.busy and self.pool_locked:
            for name in self.champion_grid.selected_names():
                self.stars[name] = value
            self.render_roster()
            self.invalidate()

    def reset(self):
        if not self.busy:
            self.locked.clear()
            self.excluded.clear()
            self.stars.clear()
            self.collab.clear()
            self.pool_locked = False
            self.champion_grid.clear_selection()
            if self.search.get():
                self.search.set('')
            self.render_roster()
            self.invalidate()

    def show_champion(self, name):
        self.champion_label.configure(text=(name if name is not None else 'Hover / arrow keys: details · Ctrl / Shift: multi-select'))
        localize_tree(self.champion_label)

    def show_unlock(self, name):
        champion = self.champions[name]
        popup = tk.Toplevel(self)
        popup.title(f'{name} — Unlock requirements')
        popup.geometry('640x380')
        popup.minsize(520, 300)
        popup.transient(self)
        body = ttk.Frame(popup, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=name, image=self.artwork.portraits[name], compound='left', padding=6, font=('Segoe UI', 15, 'bold')).pack(anchor='w')
        ttk.Label(body, text=', '.join(champion['traits']), style='Muted.TLabel', wraplength=580).pack(anchor='w', pady=(4, 10))
        attack_range = champion.get('attack_range')
        range_text = f'{attack_range:g}' if attack_range is not None else 'unavailable'
        ttk.Label(body, text=f"Role: {champion.get('role', 'Unclassified')} · Attack range: {range_text} hexes",
                  style='Muted.TLabel', wraplength=580).pack(anchor='w', pady=(0, 8))
        text_frame = ttk.Frame(body)
        text_frame.pack(fill='both', expand=True)
        text = tk.Text(text_frame, wrap='word', height=5, bg='#192337', fg='#f1d69b', font=('Segoe UI', 11), relief='flat', padx=10, pady=10)
        scroll = ttk.Scrollbar(text_frame, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        text.insert('1.0', unlock_text(champion))
        selected = self.selected_result()
        support = selected.get('champion_trait_support', {}).get(name) if selected else None
        if support is not None:
            percent = support['percent']
            text.insert('end', '\n\nPersonal trait-tier activation: ' +
                        (f'{percent:g}%' if percent is not None else 'not applicable') + '\n')
            text.insert('end', support['note'] + '\n')
            for detail in support['details']:
                suffix = 'unique — excluded from percentage' if detail['unique'] else f"{detail['tier_index']}/{detail['max_tiers']} tiers"
                text.insert('end', f"\n{detail['name']}: breakpoint {detail['reached']} · {suffix}")
        wording = display_text(tr(text.get('1.0', 'end-1c')))
        text.delete('1.0', 'end')
        text.insert('1.0', wording)
        text._preserve_text = True
        text.configure(state='disabled')
        footer = ttk.Frame(body)
        footer.pack(fill='x', pady=(12, 0))
        if champion['unlockable']:
            ttk.Button(footer, text='Requirement source', command=lambda: webbrowser.open(champion['unlock_source'])).pack(side='left')
        ttk.Button(footer, text='Close', command=popup.destroy).pack(side='right')
        localize_tree(popup)

    def freeze_inputs(self, busy):
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Combobox, ttk.Entry)):
                    child.configure(state='disabled' if busy else ('readonly' if isinstance(child, ttk.Combobox) else 'normal'))
                visit(child)
        visit(self)
        self.champion_grid.set_enabled(not busy)
        self.sync_controls()
        self.result_list.state(['disabled'] if busy else ['!disabled'])


    def run(self):
        if self.busy:
            return
        if not self.pool_locked or len(self.collab) not in POOL_SIZES:
            self.status.set('Pick 5 or 6 Collab champions and lock the pool before searching.')
            return
        ranking = {'High value': 'high_value', 'Balanced': 'balanced', 'Most active traits': 'synergies'}
        args = dict(data=self.data, collab=sorted(self.collab), size=int(self.size.get()), locked=sorted(self.locked), excluded=sorted(self.excluded), min_collab=int(self.minimum.get()), mode=ranking[self.mode.get()], sona_band='' if self.band.get() == 'None' else self.band.get(), stars=dict(self.stars))
        self.busy = True
        self.freeze_inputs(True)
        self.progress.pack(fill='x', pady=(0, 6), before=self._panes)
        self.progress.start(12)
        self.status.set('Searching end boards and planning level-by-level buildups…')
        # Snapshot inputs; the worker never reads Tk state.
        def work():
            try:
                self.messages.put(('ok', recommend_with_buildups(**args)))
            except Exception as exc:
                self.messages.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()
    def choose_temporary(self, level, slot, champion):
        """Snapshot a selected plan and replan without reading Tk in the worker."""
        result = self.selected_result()
        if self.busy or result is None:
            return
        index = self.active_result_index
        step = next((step for step in result['buildup'] if step['level'] == level), None)
        choice = next((choice for choice in step.get('temporary_options', [])
                       if choice['slot'] == slot), None) if step else None
        if choice is None or champion == choice['selected'] or champion not in choice['options']:
            return
        choices = {key: dict(value) for key, value in result.get('temporary_choices', {}).items()}
        choices.setdefault(str(level), {})[slot] = champion
        self.busy = True
        self.freeze_inputs(True)
        self.progress.pack(fill='x', pady=(0, 6), before=self._panes)
        self.progress.start(12)
        self.status.set('Updating temporary champions and all leveling boards…')
        def work():
            try:
                updated = replan_buildup(self.data, result, choices)
                self.messages.put(('replan', (index, result, updated)))
            except Exception as exc:
                self.messages.put(('replan_error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        try:
            kind, payload = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.freeze_inputs(False)
            self.progress.stop()
            self.progress.pack_forget()
            if kind in ('error', 'replan_error'):
                self.status.set(payload)
                messagebox.showerror('Cannot update build-up' if kind == 'replan_error' else 'Cannot find teams',
                                     payload, parent=self)
            elif kind == 'replan':
                index, original, updated = payload
                if index is not None and index < len(self.results) and self.results[index] is original:
                    self.results[index] = updated
                    if self.active_result_index == index:
                        self.overview.show_comp(updated, self.collab, self.locked, updated['planned_stars'])
                        self.buildup.show_comp(updated, retain_level=True)
                    self.status.set('Leveling guide updated. End goal unchanged; unlocks and item ownership remain conditional.')
            else:
                self.results = payload
                self.active_result_index = None
                self.result_list.delete(*self.result_list.get_children())
                for i, result in enumerate(payload):
                    self.result_list.insert('', 'end', iid=str(i), values=(i + 1, result['active'], result['tiers'], result['frontline'], result['cost']))
                if payload:
                    self.result_list.selection_set('0')
                    self.show_result()
                else:
                    self.overview.show_message('No team satisfies these constraints. Reduce minimum Collab, remove exclusions, or increase team slots.')
                    self.buildup.show_message('No leveling guide is available for these constraints.')
                self.status.set(f'{len(payload)} teams with level-by-level buildups. Unlock units remain conditional on completing their quests.')
            self.sync_controls()
            localize_tree(self)
        self.after(100, self.drain)

    def selected_result(self):
        selection = self.result_list.selection()
        index = int(selection[0]) if selection else None
        return self.results[index] if index is not None and index < len(self.results) else None

    def result_text(self, result):
        lines = ['TEAM', '']
        for name in result['champions']:
            c = self.champions[name]
            flags = []
            if name in self.collab or name == 'Sona': flags.append('Collab')
            if name in self.locked: flags.append('locked')
            if c['unlockable']: flags.append('unlock required')
            lines.append(f"{name}  ·  {star_text(result['planned_stars'][name])} planned  ·  {c['cost']}g" + ('  [' + ', '.join(flags) + ']' if flags else ''))
            if c['unlockable']:
                lines.append('  ' + unlock_text(c).replace('\n', '\n  '))
        lines.extend(['', f"ACTIVE TRAITS  ({result['active']} non-unique)", ''])
        for trait in sorted(result['traits'], key=lambda t: (not bool(t['tier']), t['unique'], t['name'])):
            if trait['tier']:
                lines.append(f"{trait['name']}: {trait['count']} → breakpoint {trait['tier']}" + ('  (unique)' if trait['unique'] else ''))
        lines.extend(['', 'INACTIVE / NEXT BREAKPOINT', ''])
        for trait in sorted(result['traits'], key=lambda t: t['name']):
            if trait['next'] is not None:
                lines.append(f"{trait['name']}: {trait['count']} / {trait['next']}  (+{trait['next'] - trait['count']})")
        if result['strategy_notes']:
            lines.extend(['', 'PLANNED ASSUMPTIONS', *result['strategy_notes']])
        lines.extend(['', format_buildup(result, self.champions)])
        return tr('\n'.join(lines))

    def show_result(self, _event=None):
        if self.busy:
            if (self.active_result_index is not None and self.active_result_index < len(self.results)
                    and self.result_list.selection() != (str(self.active_result_index),)):
                self.result_list.selection_set(str(self.active_result_index))
            return
        result = self.selected_result()
        if result:
            retain_level = self.buildup.result is result
            self.active_result_index = int(self.result_list.selection()[0])
            self.overview.show_comp(result, self.collab, self.locked, result['planned_stars'])
            self.buildup.show_comp(result, retain_level=retain_level)
        localize_tree(self.overview)

    def copy_team(self):
        result = self.selected_result()
        if result:
            self.clipboard_clear()
            self.clipboard_append(self.result_text(result))
            self.status.set('Team, unlock requirements, and level-by-level buildup copied.')

    def copy_tft_code(self):
        result = self.selected_result()
        if result is None:
            self.status.set('Select a suggested team before copying a TFT code.')
            return
        try:
            code = encode_team(result['champions'])
        except ValueError as exc:
            self.status.set('TFT code not copied: ' + str(exc))
            messagebox.showerror('Cannot copy TFT code', str(exc), parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self.status.set('TFT team-planner code copied. Planned stars and quest completion are not encoded.')

    def export(self):
        if not self.results:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('JSON', '*.json')], initialfile='collab-teams.json')
        if path:
            try:
                names = {name for result in self.results for name in result['champions']}
                names.update(name for result in self.results for step in result['buildup']
                             for board in ([step['board'], step['fallback'], step.get('goal_board')] +
                                           [phase['board'] for phase in step.get('upgrades', [])] +
                                           [preparation['board'] for preparation in step.get('preparation_boards', [])])
                             if board for name in board['champions'])
                unlocks = {name: {key: value for key, value in self.champions[name].items() if key.startswith('unlock_')}
                           for name in sorted(names) if self.champions[name]['unlockable']}
                Path(path).write_text(json.dumps({'version': self.data['version'], 'results': self.results, 'unlocks': unlocks}, indent=2), encoding='utf-8')
                self.status.set('Results exported to ' + path)
            except OSError as exc:
                messagebox.showerror('Export failed', str(exc), parent=self)

    def language_settings(self):
        popup = tk.Toplevel(self)
        popup.title(display_text(tr('Language settings')))
        popup.geometry('560x300')
        popup.transient(self)
        body = ttk.Frame(popup, padding=18)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Interface language', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        languages = available_languages()
        choices = ttk.Combobox(body, state='readonly', width=46,
                              values=[display_text(name) + ' [' + code + ']' for code, name in languages])
        choices._preserve_text = True
        choices.current(next(i for i, (code, _) in enumerate(languages) if code == get_language()))
        choices.pack(fill='x', pady=12)
        ttk.Label(body, text='Community-translated interface. Official localized champion names are used where available; incomplete PBE game text uses labeled community translations.',
                  wraplength=500).pack(anchor='w')
        def apply():
            set_language(languages[choices.current()][0])
            try:
                save_language(self.language_path, get_language())
            except OSError as error:
                messagebox.showerror(tr('Language setting was not saved'), str(error), parent=popup)
            self.render_roster()
            phase = self.buildup.selected_phase
            self.show_result()
            self.buildup.select_phase(phase)
            for child in self.winfo_children():
                if isinstance(child, TutorialWindow):
                    child.refresh()
            localize_tree(self)
            popup.destroy()
        ttk.Button(body, text='Apply language', command=apply).pack(anchor='e', pady=(18, 0))
        localize_tree(popup)

    def sources(self):
        popup = tk.Toplevel(self)
        popup.title('Data sources and limitations')
        popup.geometry('780x680')
        ttk.Label(popup, text=APP_TITLE + " · Developed by prokjs\n" + str(self.data['version']), wraplength=700).pack(anchor='w', padx=16, pady=12)
        tabs = ttk.Notebook(popup)
        tabs.pack(fill='both', expand=True, padx=12, pady=8)
        sources = ttk.Frame(tabs)
        legal = ttk.Frame(tabs)
        tabs.add(sources, text='Data sources')
        tabs.add(legal, text='Legal and public-release requirements')
        for source in self.data['sources']:
            ttk.Button(sources, text=source['name'], command=lambda url=source['url']: webbrowser.open(url)).pack(anchor='w', padx=16, pady=4)
        ttk.Label(sources, text=CONTENT_NOTICE, wraplength=700, style='Muted.TLabel').pack(anchor='w', padx=16, pady=12)
        ttk.Label(sources, text='Pre-release community data can change. No game client access, automatic scouting, or runtime network connection is required. Stars and item holders are planning assumptions, not owned upgrades or inventory.',
                  wraplength=700).pack(anchor='w', padx=16, pady=12)
        for title, url in POLICY_LINKS:
            ttk.Button(legal, text=title, command=lambda url=url: webbrowser.open(url)).pack(anchor='w', padx=8, pady=3)
        text = tk.Text(legal, wrap='word', bg='#192337', fg='#e5e7eb',
                       relief='flat', padx=12, pady=10, font=('Segoe UI', 10))
        text.pack(fill='both', expand=True, pady=8)
        text._preserve_text = True
        text.tag_configure('heading', foreground='#c4b5fd', font=('Segoe UI', 11, 'bold'), spacing1=10)
        for heading, wording in (
            ('Riot notice', RIOT_NOTICE), ('Purpose — not winning odds', PURPOSE_NOTICE),
            ('Use at your own risk', LIABILITY_NOTICE), ('Public launch is not cleared', PROTOTYPE_NOTICE),
            ('Public-release requirements', '\n\n'.join(PUBLIC_RELEASE_REQUIREMENTS)),
        ):
            text.insert('end', display_text(tr(heading)) + '\n', 'heading')
            text.insert('end', (wording if heading == 'Riot notice' else display_text(tr(wording))) + '\n\n')
        text.configure(state='disabled')
        ttk.Button(legal, text='Third-party notices',
                   command=lambda: webbrowser.open((self.bundle / 'THIRD_PARTY_NOTICES.txt').as_uri())).pack(anchor='w', padx=8, pady=4)
        localize_tree(popup)


if __name__ == '__main__':
    App().mainloop()
