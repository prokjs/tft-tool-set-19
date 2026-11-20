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
from buildup import recommend_with_buildups, format_buildup
from buildup_view import BuildupWindow
from team_code import encode_team


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("prokjs's Set 19 TFT tool")
        self.geometry('1280x850')
        self.minsize(1060, 720)
        if sys.platform == 'win32':
            self.state('zoomed')
        self.configure(bg='#111827')
        self.bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
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
        self.after(100, self.drain)
        self.size.trace_add('write', self.change_level)
        for variable in (self.minimum, self.mode, self.band):
            variable.trace_add('write', lambda *_: self.invalidate())

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
        ttk.Label(brand, text="prokjs's Set 19 TFT tool", style='Title.TLabel').pack(anchor='w')
        ttk.Label(brand, text='Developed by prokjs · Offline team explorer', style='Muted.TLabel').pack(anchor='w')
        controls = ttk.Frame(header)
        controls.grid(row=0, column=1, sticky='e', padx=(14, 0))
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
            wrapped = event.width < brand.winfo_reqwidth() + controls.winfo_reqwidth() + 14
            row = 1 if wrapped else 0
            if int(controls.grid_info()['row']) != row:
                controls.grid(row=row, column=0 if wrapped else 1,
                              columnspan=2 if wrapped else 1, sticky='w' if wrapped else 'e',
                              padx=0 if wrapped else (14, 0), pady=(8, 0) if wrapped else 0)
        header.bind('<Configure>', arrange_header)
        self.progress = ttk.Progressbar(root, mode='indeterminate', style='Slim.Horizontal.TProgressbar')
        ttk.Label(root, textvariable=self.status, wraplength=1150).pack(side='bottom', anchor='w', pady=(10, 0))
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
                panes.sashpos(0, min(440, max(320, int(event.width * 0.33))))
        panes.bind('<Configure>', arrange_panes)
        ttk.Label(left, text='YOUR GAME', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.pool_button = ttk.Button(left, text='Lock Collab pool', command=self.toggle_pool_lock)
        self.pool_button.pack(fill='x', pady=6)
        self.pool_hint = ttk.Label(left, wraplength=440, foreground='#f1d69b')
        self.pool_hint.pack(fill='x', pady=(0, 4))
        self.pool_label = ttk.Label(left, wraplength=440, style='Muted.TLabel')
        self.pool_label.pack(fill='x', pady=(0, 8))
        self.pool_strip = tk.Text(left, height=2, width=1, bg='#111827', relief='flat', state='disabled', cursor='arrow')
        self.pool_strip.pack(fill='x', pady=(0, 6))
        ttk.Label(left, text='Search champions or traits', style='Muted.TLabel').pack(anchor='w')
        entry = ttk.Entry(left, textvariable=self.search)
        entry.pack(fill='x', pady=(0, 6))
        self.search.trace_add('write', lambda *_: self.render_roster())
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
        self.champion_label = ttk.Label(left, wraplength=440, style='Muted.TLabel')
        self.champion_label.pack(fill='x', pady=8)
        roster_help = ttk.Label(left, text='C = Collab   L = team locked   X = excluded\nPool unlocked: click to add/remove picks. Sona is automatic.\nPool locked: Ctrl / Shift multi-select for team controls.\nUnlock tasks still apply; hover/select to see requirements.\nK/DA uses stars; Baron Nashor occupies two slots.', style='Muted.TLabel', wraplength=450)
        roster_help.pack(anchor='w')
        def wrap_roster_details(event):
            self.pool_hint.configure(wraplength=max(180, event.width - 28))
            self.pool_label.configure(wraplength=max(180, event.width - 28))
            self.champion_label.configure(wraplength=max(160, event.width - 84))
            roster_help.configure(wraplength=max(180, event.width - 28))
        left.bind('<Configure>', wrap_roster_details)
        ttk.Label(right, text='SUGGESTED TEAMS', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        result_frame = ttk.Frame(right)
        result_frame.pack(fill='x', pady=6)
        self.result_list = ttk.Treeview(result_frame, columns=('rank', 'active', 'tiers', 'front', 'cost'), show='headings', selectmode='browse', height=3)
        for key, title, width in [('rank', 'Rank', 50), ('active', 'Active traits', 100), ('tiers', 'Trait tiers', 90), ('front', 'Frontline', 85), ('cost', 'Gold', 65)]:
            self.result_list.heading(key, text=title)
            self.result_list.column(key, width=width, stretch=True)
        result_scroll = ttk.Scrollbar(result_frame, orient='vertical', command=self.result_list.yview)
        self.result_list.configure(yscrollcommand=result_scroll.set)
        self.result_list.pack(side='left', fill='x', expand=True)
        result_scroll.pack(side='right', fill='y')
        self.result_list.bind('<<TreeviewSelect>>', self.show_result)
        self.overview = CompOverview(right, self.data, self.artwork, self.show_unlock)
        disclaimer = ttk.Label(right, text='Approximate search, not a guaranteed optimum. High value rewards base champion cost, synergies, and frontline balance; cost is not measured strength. Items, augments, and combat are not simulated. Active totals exclude unique traits.', style='Muted.TLabel', wraplength=610)
        disclaimer.pack(side='bottom', fill='x')
        right.bind('<Configure>', lambda event: disclaimer.configure(wraplength=max(240, event.width - 8)))
        actions = ttk.Frame(right)
        actions.pack(side='bottom', fill='x', pady=8)
        self.buildup_button = ttk.Button(actions, text='Level buildup', command=self.show_buildup, state='disabled')
        self.buildup_button.pack(side='left', padx=(0, 6))
        ttk.Button(actions, text='Copy TFT code', command=self.copy_tft_code).pack(side='left')
        ttk.Button(actions, text='Copy plan', command=self.copy_team).pack(side='left', padx=6)
        ttk.Button(actions, text='Save JSON', command=self.export).pack(side='left')
        ttk.Button(actions, text='Data sources', command=self.sources).pack(side='right')
        self.overview.pack(fill='both', expand=True)

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
        else:
            self.render_roster()
        self.invalidate()

    def sync_controls(self):
        valid_pool = len(self.collab) in POOL_SIZES
        self.pool_button.configure(text='Unlock Collab pool' if self.pool_locked else 'Lock Collab pool')
        self.pool_button.state(['disabled'] if self.busy or (not self.pool_locked and not valid_pool) else ['!disabled'])
        for button in self.team_controls:
            button.state(['disabled'] if self.busy or not self.pool_locked else ['!disabled'])
        self.run_button.state(['!disabled'] if self.pool_locked and valid_pool and not self.busy else ['disabled'])
        self.buildup_button.state(['!disabled'] if self.results and not self.busy else ['disabled'])

    def invalidate(self):
        self.results = []
        self.result_list.delete(*self.result_list.get_children())
        self.buildup_button.state(['disabled'])
        self.overview.show_message('Use the main portrait grid to pick your Collab pool.\n\nWhile unlocked, click to add/remove champions. Conflicting candidates disappear; selected picks remain removable. Sona is automatic.\n\nLock a 5- or 6-champion pool to restore the full roster and enable team locks, exclusions, star controls, and recommendations. Unlock the pool to edit it again.')
        self.sync_controls()
        self.status.set('Pool locked. Select team units or find recommendations.' if self.pool_locked else f'Pool unlocked: {len(self.collab)} picked. Choose 5 or 6, then lock the pool.')

    def change_level(self, *_):
        self.render_roster()
        self.invalidate()

    def render_roster(self):
        query = self.search.get().casefold().strip()
        entries = []
        visible = visible_champions(self.champions, self.collab, self.pool_locked)
        effective_stars = planned_stars(self.champions, int(self.size.get()), self.stars)
        for c in sorted(self.champions.values(), key=lambda c: (c['cost'], c['name'])):
            name = c['name']
            if name not in visible:
                continue
            if query not in (' '.join([name] + c['traits'])).casefold():
                continue
            flags = 'C ' if name in self.collab or name == 'Sona' else ''
            if self.pool_locked:
                flags += ('L ' if name in self.locked else '') + ('X' if name in self.excluded else '')
            entries.append({**c, 'flags': flags.strip(), 'stars': effective_stars[name],
                            'pool_member': not self.pool_locked and name in self.collab,
                            'fixed': not self.pool_locked and name == 'Sona'})
        self.champion_grid.set_entries(entries)
        self.pool_hint.configure(text=(f'LOCKED · {len(self.collab)} picks + Sona. Full team-building roster.' if self.pool_locked else f'UNLOCKED · {len(self.collab)}/{max(POOL_SIZES)} picks + Sona. Click portraits to add/remove.'))
        self.pool_label.configure(text='Collab: ' + ', '.join(sorted(self.collab)) + '  + Sona')
        self.pool_strip.configure(state='normal')
        self.pool_strip.delete('1.0', 'end')
        for name in sorted(self.collab) + ['Sona']:
            self.pool_strip.image_create('end', image=self.artwork.champions[name], padx=3, pady=3)
        self.pool_strip.configure(state='disabled')
        self.sync_controls()

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
        if name is None:
            self.champion_label.configure(image='', text='Click portraits to pick your Collab pool.' if not self.pool_locked else 'Select portraits for team controls; hover for champion details.')
        else:
            c = self.champions[name]
            self.champion_label.configure(image=self.artwork.portraits[name], compound='left', padding=(0, 0, 6, 0), text=f"  {name} — {', '.join(c['traits'])}\n" + unlock_text(c))

    def show_unlock(self, name):
        champion = self.champions[name]
        popup = tk.Toplevel(self)
        popup.title(name + ' — Unlock requirements')
        popup.geometry('640x380')
        popup.minsize(520, 300)
        popup.transient(self)
        body = ttk.Frame(popup, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=name, image=self.artwork.portraits[name], compound='left', padding=6, font=('Segoe UI', 15, 'bold')).pack(anchor='w')
        ttk.Label(body, text=', '.join(champion['traits']), style='Muted.TLabel', wraplength=580).pack(anchor='w', pady=(4, 10))
        text_frame = ttk.Frame(body)
        text_frame.pack(fill='both', expand=True)
        text = tk.Text(text_frame, wrap='word', height=5, bg='#192337', fg='#f1d69b', font=('Segoe UI', 11), relief='flat', padx=10, pady=10)
        scroll = ttk.Scrollbar(text_frame, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        text.insert('1.0', unlock_text(champion))
        text.configure(state='disabled')
        footer = ttk.Frame(body)
        footer.pack(fill='x', pady=(12, 0))
        if champion['unlockable']:
            ttk.Button(footer, text='Requirement source', command=lambda: webbrowser.open(champion['unlock_source'])).pack(side='left')
        ttk.Button(footer, text='Close', command=popup.destroy).pack(side='right')

    def freeze_inputs(self, busy):
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Combobox, ttk.Entry)):
                    child.configure(state='disabled' if busy else ('readonly' if isinstance(child, ttk.Combobox) else 'normal'))
                visit(child)
        visit(self)
        self.champion_grid.set_enabled(not busy)
        self.sync_controls()


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
            if kind == 'error':
                self.status.set(payload)
                messagebox.showerror('Cannot find teams', payload, parent=self)
            else:
                self.results = payload
                self.buildup_button.state(['!disabled'] if payload else ['disabled'])
                self.result_list.delete(*self.result_list.get_children())
                for i, result in enumerate(payload):
                    self.result_list.insert('', 'end', iid=str(i), values=(i + 1, result['active'], result['tiers'], result['frontline'], result['cost']))
                if payload:
                    self.result_list.selection_set('0')
                    self.show_result()
                else:
                    self.overview.show_message('No team satisfies these constraints. Reduce minimum Collab, remove exclusions, or increase team slots.')
                self.status.set(f'{len(payload)} teams with level-by-level buildups. Unlock units remain conditional on completing their quests.')
        self.after(100, self.drain)

    def selected_result(self):
        selection = self.result_list.selection()
        return self.results[int(selection[0])] if selection and self.results else None

    def show_buildup(self):
        result = self.selected_result()
        if result:
            BuildupWindow(self, result, self.data, self.artwork, self.show_unlock)

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
        return '\n'.join(lines)

    def show_result(self, _event=None):
        result = self.selected_result()
        if result:
            self.overview.show_comp(result, self.collab, self.locked, result['planned_stars'])

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
                             for board in (step['board'], step['fallback']) if board
                             for name in board['champions'])
                unlocks = {name: {key: value for key, value in self.champions[name].items() if key.startswith('unlock_')}
                           for name in sorted(names) if self.champions[name]['unlockable']}
                Path(path).write_text(json.dumps({'version': self.data['version'], 'results': self.results, 'unlocks': unlocks}, indent=2), encoding='utf-8')
                self.status.set('Results exported to ' + path)
            except OSError as exc:
                messagebox.showerror('Export failed', str(exc), parent=self)

    def sources(self):
        popup = tk.Toplevel(self)
        popup.title('Data sources and limitations')
        popup.geometry('700x520')
        ttk.Label(popup, text="prokjs's Set 19 TFT tool · Developed by prokjs\n" + str(self.data['version']), wraplength=610).pack(anchor='w', padx=16, pady=12)
        for source in self.data['sources']:
            ttk.Button(popup, text=source['name'], command=lambda url=source['url']: webbrowser.open(url)).pack(anchor='w', padx=16, pady=4)
        ttk.Label(popup, text='Champion and trait artwork © Riot Games. Set 19 images sourced from TFT Ninja; bundled for offline use.', wraplength=610, style='Muted.TLabel').pack(anchor='w', padx=16, pady=6)
        ttk.Label(popup, text='Pre-release community data can change. No game client access, automatic scouting, or network connection is required. Stars are planned upgrade targets, including automatic late-game tank targets unless overridden; emblems and conditional/prismatic unlocks are not simulated.', wraplength=610).pack(anchor='w', padx=16, pady=12)


if __name__ == '__main__':
    App().mainloop()
