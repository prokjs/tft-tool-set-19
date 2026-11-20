"""Pick the five current Collab champions; Sona is automatic.

Each pick removes every candidate sharing a native trait with any selected unit.
The main team-building roster is deliberately not filtered by these pool rules.
"""
import tkinter as tk
from tkinter import ttk
from champion_info import unlock_text


def eligible_champions(champions, selected):
    """Return unselected, nonconflicting names in cost/name order; cap at five."""
    if len(selected) >= 5:
        return []
    traits = {trait for name in selected for trait in champions[name]['traits'] if trait != 'Collab'}
    exclusive = {champions[name]['exclusive_group'] for name in selected if champions[name].get('exclusive_group')}
    return [c['name'] for c in sorted(champions.values(), key=lambda c: (c['cost'], c['name']))
            if c['name'] != 'Sona' and c['name'] not in selected
            and not traits.intersection(c['traits'])
            and (not c.get('exclusive_group') or c['exclusive_group'] not in exclusive)]


class PoolPicker(tk.Toplevel):
    def __init__(self, parent, champions, artwork, selected, on_save):
        super().__init__(parent)
        self.title('Load your current Collab pool')
        self.geometry('1040x800')
        self.minsize(900, 760)
        self.configure(bg='#111827')
        self.transient(parent)
        self.champions = champions
        self.artwork = artwork
        self.selected = list(sorted(selected))
        self.on_save = on_save
        self.search = tk.StringVar()
        self.summary = tk.StringVar()
        root = ttk.Frame(self, padding=16)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='Pick the five Collab champions shown in your game', font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(root, text='Shared native traits are filtered after every pick. Sona is always included separately.', style='Muted.TLabel').pack(anchor='w', pady=(6, 12))
        ttk.Label(root, text='Search available champions or traits').pack(anchor='w')
        search_entry = ttk.Entry(root, textvariable=self.search)
        search_entry.pack(fill='x', pady=5)
        ttk.Label(root, textvariable=self.summary).pack(anchor='w', pady=5)
        panes = ttk.Panedwindow(root, orient='horizontal')
        panes.pack(fill='both', expand=True)
        available = ttk.Frame(panes, padding=(0, 0, 10, 0))
        picked = ttk.Frame(panes)
        panes.add(available, weight=1)
        panes.add(picked, weight=1)
        ttk.Label(available, text='AVAILABLE', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=6)
        self.available = self.make_tree(available)
        ttk.Button(available, text='Add selected champion →', command=self.add).pack(fill='x', pady=8)
        ttk.Label(picked, text='CURRENT POOL  ·  + Sona automatically', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=6)
        self.picked = self.make_tree(picked)
        ttk.Button(picked, text='← Remove selected champion', command=self.remove).pack(fill='x', pady=8)
        details = ttk.LabelFrame(root, text='Champion unlock details', padding=8)
        details.pack(fill='x', pady=(8, 0))
        self.detail_name = tk.StringVar()
        ttk.Label(details, textvariable=self.detail_name, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        detail_body = ttk.Frame(details)
        detail_body.pack(fill='x', pady=(4, 0))
        self.detail_text = tk.Text(detail_body, height=6, wrap='word', state='disabled',
                                   bg='#192337', fg='#ffffff', font=('Segoe UI', 10),
                                   relief='flat', padx=8, pady=6)
        detail_scroll = ttk.Scrollbar(detail_body, orient='vertical', command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.pack(side='left', fill='x', expand=True)
        detail_scroll.pack(side='right', fill='y')
        footer = ttk.Frame(root)
        footer.pack(fill='x', pady=(8, 0))
        ttk.Button(footer, text='Clear picks', command=self.clear).pack(side='left')
        ttk.Button(footer, text='Cancel', command=self.destroy).pack(side='right')
        self.save_button = ttk.Button(footer, text='Load pool', style='Accent.TButton', command=self.save)
        self.save_button.pack(side='right', padx=8)
        self.available.bind('<Double-1>', lambda _: self.add())
        self.picked.bind('<Double-1>', lambda _: self.remove())
        self.available.bind('<<TreeviewSelect>>', lambda _: self.show_selection(self.available))
        self.picked.bind('<<TreeviewSelect>>', lambda _: self.show_selection(self.picked))
        self.search.trace_add('write', lambda *_: self.refresh())
        self.refresh()
        self.grab_set()
        search_entry.focus_set()

    def make_tree(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=('name', 'traits'), show='tree headings', selectmode='browse', style='Roster.Treeview', height=5)
        tree.column('#0', width=56, minwidth=56, stretch=False)
        tree.heading('name', text='Champion')
        tree.column('name', width=125, minwidth=100, stretch=False)
        tree.heading('traits', text='Native traits')
        tree.column('traits', width=240, minwidth=130)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        return tree

    def show_selection(self, tree):
        selection = tree.selection()
        if selection:
            self.show_details(selection[0])

    def show_details(self, name):
        self.detail_name.set(name + (' · Automatically included' if name == 'Sona' else ''))
        self.detail_text.configure(state='normal')
        self.detail_text.delete('1.0', 'end')
        self.detail_text.insert('1.0', unlock_text(self.champions[name]))
        self.detail_text.configure(state='disabled')
        self.detail_text.yview_moveto(0)

    def refresh(self):
        self.available.delete(*self.available.get_children())
        self.picked.delete(*self.picked.get_children())
        query = self.search.get().casefold().strip()
        eligible = eligible_champions(self.champions, self.selected)
        shown = 0
        for tree, names in [(self.available, eligible), (self.picked, self.selected)]:
            for name in names:
                champion = self.champions[name]
                if tree == self.available and query not in ' '.join([name] + champion['traits']).casefold():
                    continue
                tree.insert('', 'end', iid=name, image=self.artwork.champions[name], values=(name, ', '.join(champion['traits'])))
                if tree == self.available:
                    shown += 1
        self.summary.set(f'{len(self.selected)}/5 picked + Sona  ·  {len(eligible)} eligible  ·  {shown} matching search' + ('  ·  Pool complete' if len(self.selected) == 5 else ''))
        self.save_button.state(['!disabled'] if len(self.selected) == 5 else ['disabled'])
        self.show_details('Sona')

    def add(self):
        selection = self.available.selection()
        if selection and selection[0] in eligible_champions(self.champions, self.selected):
            self.selected.append(selection[0])
            self.refresh()

    def remove(self):
        selection = self.picked.selection()
        if selection:
            self.selected.remove(selection[0])
            self.refresh()

    def clear(self):
        self.selected.clear()
        self.refresh()

    def save(self):
        if len(self.selected) == 5:
            self.on_save(set(self.selected))
            self.destroy()
