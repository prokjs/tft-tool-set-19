"""Offline Collab optimizer: manually load five disjoint-trait champions + Sona.

The pool picker filters shared native traits after each pick; support champions
remain unrestricted in the main roster. Only complete five-unit pools can search.
"""
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import webbrowser

from optimizer import load_data, optimize, BAND_TRAITS
from visuals import Artwork, COST_COLORS
from pool_picker import PoolPicker
from champion_grid import ChampionGrid
from trait_rows import TraitRow, badge_style, STYLE_ORDER
from champion_info import unlock_text


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
        self.detail_widgets = []
        self.card_columns = 1
        self.champions = {c['name']: c for c in self.data['champions']}
        self.collab, self.locked, self.excluded = set(), set(), set()
        self.stars = {}
        self.results = []
        self.messages = queue.Queue()
        self.busy = False
        self.size = tk.StringVar(value='8')
        self.minimum = tk.StringVar(value='3')
        self.mode = tk.StringVar(value='High value')
        self.band = tk.StringVar(value='None')
        self.search = tk.StringVar()
        self.status = tk.StringVar(value='Load the five Collab champions from your current game.')
        self._style()
        self._layout()
        self.render_roster()
        self.invalidate()
        self.after(100, self.drain)
        for variable in (self.size, self.minimum, self.mode, self.band):
            variable.trace_add('write', lambda *_: self.invalidate())

    def _style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background='#111827', foreground='#e5e7eb', font=('Segoe UI', 10))
        style.configure('TFrame', background='#111827')
        style.configure('TLabel', background='#111827')
        style.configure('Muted.TLabel', foreground='#9ca3af')
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'), foreground='#c4b5fd')
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

    def _layout(self):
        root = ttk.Frame(self, padding=20)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text="prokjs's Set 19 TFT tool", style='Title.TLabel').pack(anchor='w')
        ttk.Label(root, text='TFT SET 19  /  OFFLINE TEAM EXPLORER  ·  Developed by prokjs', style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        ttk.Label(root, text=str(self.data['version']), style='Muted.TLabel').pack(anchor='w')
        controls = ttk.Frame(root)
        controls.pack(fill='x', pady=12)
        for col, (label, variable, values, width) in enumerate([
            ('Team slots', self.size, list(map(str, range(3, 12))), 8),
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
        self.progress = ttk.Progressbar(root, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 8))
        panes = ttk.Panedwindow(root, orient='horizontal')
        panes.pack(fill='both', expand=True)
        left = ttk.Frame(panes, padding=(0, 0, 14, 0))
        right = ttk.Frame(panes)
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        ttk.Label(left, text='YOUR GAME', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.pool_button = ttk.Button(left, text='Load current Collab pool', command=self.edit_pool)
        self.pool_button.pack(fill='x', pady=6)
        self.pool_label = ttk.Label(left, wraplength=440, style='Muted.TLabel')
        self.pool_label.pack(fill='x', pady=(0, 8))
        self.pool_strip = tk.Text(left, height=2, bg='#111827', relief='flat', state='disabled', cursor='arrow')
        self.pool_strip.pack(fill='x', pady=(0, 6))
        ttk.Label(left, text='Search champions or traits', style='Muted.TLabel').pack(anchor='w')
        entry = ttk.Entry(left, textvariable=self.search)
        entry.pack(fill='x', pady=(0, 6))
        self.search.trace_add('write', lambda *_: self.render_roster())
        roster_frame = ttk.Frame(left)
        roster_frame.pack(fill='both', expand=True)
        self.champion_grid = ChampionGrid(roster_frame, self.artwork, self.show_champion)
        self.champion_grid.pack(fill='both', expand=True)
        buttons = ttk.Frame(left)
        buttons.pack(fill='x', pady=7)
        for text, action in [('Lock', 'locked'), ('Exclude', 'excluded')]:
            ttk.Button(buttons, text=text, command=lambda a=action: self.toggle(a)).pack(side='left', padx=(0, 5))
        star_row = ttk.Frame(left)
        star_row.pack(fill='x')
        ttk.Label(star_row, text='Set selected stars:').pack(side='left')
        for star in range(1, 5):
            ttk.Button(star_row, text=str(star), width=2, command=lambda s=star: self.set_stars(s)).pack(side='left', padx=2)
        ttk.Button(star_row, text='Reset', command=self.reset).pack(side='right')
        self.champion_label = ttk.Label(left, wraplength=440, style='Muted.TLabel')
        self.champion_label.pack(fill='x', pady=8)
        ttk.Label(left, text='C = Collab   L = locked   X = excluded\nClick portraits; Ctrl / Shift for multi-select. Hover for details.\nUnlockable champions are candidates: exclude any unavailable.\nSona requires her unlock + level 10; exclude her if unavailable.\nK/DA uses stars; Baron Nashor occupies two slots.', style='Muted.TLabel', wraplength=450).pack(anchor='w')
        ttk.Label(right, text='SUGGESTED TEAMS', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        result_frame = ttk.Frame(right)
        result_frame.pack(fill='x', pady=6)
        self.result_list = ttk.Treeview(result_frame, columns=('rank', 'active', 'tiers', 'front', 'cost'), show='headings', selectmode='browse', height=4)
        for key, title, width in [('rank', 'Rank', 50), ('active', 'Active traits', 100), ('tiers', 'Trait tiers', 90), ('front', 'Frontline', 85), ('cost', 'Gold', 65)]:
            self.result_list.heading(key, text=title)
            self.result_list.column(key, width=width, stretch=True)
        result_scroll = ttk.Scrollbar(result_frame, orient='vertical', command=self.result_list.yview)
        self.result_list.configure(yscrollcommand=result_scroll.set)
        self.result_list.pack(side='left', fill='x', expand=True)
        result_scroll.pack(side='right', fill='y')
        self.result_list.bind('<<TreeviewSelect>>', self.show_result)
        detail_frame = ttk.Frame(right)
        detail_frame.pack(fill='both', expand=True)
        self.detail = tk.Text(detail_frame, wrap='word', bg='#192337', fg='#e5e7eb', relief='flat', padx=16, pady=12, font=('Segoe UI', 11), state='disabled', height=6)
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.pack(side='left', fill='both', expand=True)
        detail_scroll.pack(side='right', fill='y')
        self.detail.tag_configure('heading', foreground='#c4b5fd', font=('Segoe UI', 12, 'bold'), spacing1=10, spacing3=8)
        self.detail.tag_configure('unlock', foreground='#f1d69b', spacing3=6)
        self.detail.tag_configure('unlock_name', foreground='#f1d69b', font=('Segoe UI', 10, 'bold'), spacing1=8)
        self.detail.bind('<Configure>', self.resize_cards)
        actions = ttk.Frame(right)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Copy team', command=self.copy_team).pack(side='left')
        ttk.Button(actions, text='Export results', command=self.export).pack(side='left', padx=6)
        ttk.Button(actions, text='Data sources', command=self.sources).pack(side='right')
        ttk.Label(right, text='Approximate search, not a guaranteed optimum. High value rewards base champion cost, synergies, and frontline balance; cost is not measured strength. Items, augments, and combat are not simulated. Active totals exclude unique traits.', style='Muted.TLabel', wraplength=610).pack(fill='x')
        ttk.Label(root, textvariable=self.status, wraplength=1150).pack(anchor='w', pady=(10, 0))

    def edit_pool(self):
        if not self.busy:
            PoolPicker(self, self.champions, self.artwork, self.collab, self.apply_pool)

    def apply_pool(self, selected):
        self.collab = selected
        self.render_roster()
        self.invalidate()

    def invalidate(self):
        self.results = []
        self.result_list.delete(*self.result_list.get_children())
        self.set_detail('Load your current pool: five champions with no shared native traits, plus Sona automatically.\n\nThe pool picker narrows available champions after each choice. Remove a pick to restore eligible candidates.\n\nThen lock existing team units or exclude unavailable units in the unrestricted main roster, and choose Find teams.')
        ready = len(self.collab) == 5
        self.run_button.state(['!disabled'] if ready and not self.busy else ['disabled'])
        self.status.set('Pool loaded. Find teams to refresh recommendations.' if ready else 'Load five Collab champions to enable team search.')

    def render_roster(self):
        query = self.search.get().casefold().strip()
        entries = []
        for c in sorted(self.champions.values(), key=lambda c: (c['cost'], c['name'])):
            name = c['name']
            if query not in (' '.join([name] + c['traits'])).casefold():
                continue
            flags = ('C ' if name in self.collab or name == 'Sona' else '') + ('L ' if name in self.locked else '') + ('X' if name in self.excluded else '')
            entries.append({**c, 'flags': flags.strip(), 'stars': self.stars.get(name, 1)})
        self.champion_grid.set_entries(entries)
        self.pool_button.configure(text=f"Edit current Collab pool · {len(self.collab)}/5 + Sona")
        self.pool_label.configure(text='Collab: ' + ', '.join(sorted(self.collab)) + '  + Sona')
        self.pool_strip.configure(state='normal')
        self.pool_strip.delete('1.0', 'end')
        for name in sorted(self.collab) + ['Sona']:
            self.pool_strip.image_create('end', image=self.artwork.champions[name], padx=3, pady=3)
        self.pool_strip.configure(state='disabled')

    def toggle(self, target):
        if self.busy:
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
        if not self.busy:
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
            self.render_roster()
            self.invalidate()

    def show_champion(self, name):
        if name is None:
            self.champion_label.configure(image='', text='Click a portrait to select; hover for champion details.')
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


    def run(self):
        if self.busy:
            return
        if len(self.collab) != 5:
            self.status.set('Load exactly five Collab champions before searching.')
            return
        ranking = {'High value': 'high_value', 'Balanced': 'balanced', 'Most active traits': 'synergies'}
        args = dict(data=self.data, collab=sorted(self.collab), size=int(self.size.get()), locked=sorted(self.locked), excluded=sorted(self.excluded), min_collab=int(self.minimum.get()), mode=ranking[self.mode.get()], sona_band='' if self.band.get() == 'None' else self.band.get(), stars=dict(self.stars))
        self.busy = True
        self.freeze_inputs(True)
        self.progress.start(12)
        self.status.set('Searching teams…')
        # Snapshot inputs; the worker never reads Tk state.
        def work():
            try:
                self.messages.put(('ok', optimize(**args)))
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
            if kind == 'error':
                self.status.set(payload)
                messagebox.showerror('Cannot find teams', payload, parent=self)
            else:
                self.results = payload
                self.result_list.delete(*self.result_list.get_children())
                for i, result in enumerate(payload):
                    self.result_list.insert('', 'end', iid=str(i), values=(i + 1, result['active'], result['tiers'], result['frontline'], result['cost']))
                if payload:
                    self.result_list.selection_set('0')
                    self.show_result()
                else:
                    self.set_detail('No team satisfies these constraints. Reduce minimum Collab, remove exclusions, or increase team slots.')
                self.status.set(f'{len(payload)} teams found. Approximate synergy ranking; no measured win rates.')
        self.after(100, self.drain)

    def selected_result(self):
        selection = self.result_list.selection()
        return self.results[int(selection[0])] if selection and self.results else None

    def result_text(self, result):
        lines = ['TEAM', '']
        for name in result['champions']:
            c = self.champions[name]
            flags = []
            if name in self.collab or name == 'Sona': flags.append('Collab')
            if name in self.locked: flags.append('locked')
            if c['unlockable']: flags.append('unlock required')
            lines.append(f"{name}  ·  {self.stars.get(name, 1)}★  ·  {c['cost']}g" + ('  [' + ', '.join(flags) + ']' if flags else ''))
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
        return '\n'.join(lines)

    def show_result(self, _event=None):
        result = self.selected_result()
        if result:
            self.set_detail('')
            self.detail.configure(state='normal')
            self.detail.insert('end', 'TEAM\n', 'heading')
            self.card_columns = max(1, (self.detail.winfo_width() - 36) // 112)
            for index, name in enumerate(result['champions']):
                champion = self.champions[name]
                color = COST_COLORS[champion['cost']]
                card = tk.Frame(self.detail, bg='#192337', highlightbackground=color, highlightthickness=1, padx=3, pady=4, width=100)
                tk.Label(card, image=self.artwork.portraits[name], bg='#192337').pack()
                tk.Label(card, text=name, width=13, wraplength=94, fg='#e5e7eb', bg='#192337', font=('Segoe UI', 9, 'bold')).pack()
                badges = f"{self.stars.get(name, 1)}★  {champion['cost']}g"
                if name in self.collab or name == 'Sona':
                    badges += ' · C'
                if name in self.locked:
                    badges += ' · L'
                tk.Label(card, text=badges, fg=color, bg='#192337', font=('Segoe UI', 9)).pack()
                if champion['unlockable']:
                    tk.Button(card, text='Unlock details', command=lambda name=name: self.show_unlock(name), fg='#f1d69b', bg='#192337', activebackground='#253047', activeforeground='#ffffff', font=('Segoe UI', 8, 'underline'), borderwidth=0, cursor='hand2').pack()
                self.detail_widgets.append(card)
                self.detail.window_create('end', window=card, padx=3, pady=4, align='top')
                if (index + 1) % self.card_columns == 0:
                    self.detail.insert('end', '\n')
            definitions = {trait['name']: trait for trait in self.data['traits']}
            unique_active = sum(bool(trait['tier']) and trait['unique'] for trait in result['traits'])
            self.detail.insert('end', f"\nTRAITS  ·  {result['active']} active + {unique_active} unique\n", 'heading')
            rows = sorted(result['traits'], key=lambda trait: (
                STYLE_ORDER[badge_style(trait, definitions[trait['name']])],
                -trait['count'], trait['name'],
            ))
            for trait in rows:
                row = TraitRow(self.detail, trait, definitions[trait['name']], self.artwork)
                self.detail_widgets.append(row)
                self.detail.window_create('end', window=row, padx=2, pady=1, align='top')
                self.detail.insert('end', '\n')
            unlockable_names = [name for name in result['champions'] if self.champions[name]['unlockable']]
            if unlockable_names:
                self.detail.insert('end', '\nUNLOCK REQUIREMENTS\n', 'heading')
                for name in unlockable_names:
                    self.detail.insert('end', name + '\n', 'unlock_name')
                    self.detail.insert('end', unlock_text(self.champions[name]) + '\n', 'unlock')
            self.detail.configure(state='disabled')
            self.detail.yview_moveto(0)


    def resize_cards(self, event):
        if max(1, (event.width - 36) // 112) != self.card_columns and self.results:
            self.show_result()

    def set_detail(self, text):
        for widget in self.detail_widgets:
            widget.destroy()
        self.detail_widgets.clear()
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def copy_team(self):
        result = self.selected_result()
        if result:
            self.clipboard_clear()
            self.clipboard_append(self.result_text(result))
            self.status.set('Team and trait breakpoints copied.')

    def export(self):
        if not self.results:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.json', filetypes=[('JSON', '*.json')], initialfile='collab-teams.json')
        if path:
            try:
                unlocks = {name: {key: value for key, value in self.champions[name].items() if key.startswith('unlock_')}
                           for name in sorted({name for result in self.results for name in result['champions']})
                           if self.champions[name]['unlockable']}
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
        ttk.Label(popup, text='Pre-release community data can change. No game client access, automatic scouting, or network connection is required. Star levels are manual inputs; emblems and conditional/prismatic unlocks are not simulated.', wraplength=610).pack(anchor='w', padx=16, pady=12)


if __name__ == '__main__':
    App().mainloop()
