"""Scrollable portrait-only roster with search-safe selection and keyboard control."""
import tkinter as tk
from tkinter import ttk
from visuals import COST_COLORS


class ChampionGrid(tk.Frame):
    TILE_WIDTH = 84
    TILE_HEIGHT = 96

    def __init__(self, parent, artwork, on_select):
        super().__init__(parent, bg='#192337')
        self.artwork = artwork
        self.on_select = on_select
        self.entries = {}
        self.names = []
        self.tiles = {}
        self.selected = set()
        self.anchor = None
        self.focused = None
        self.columns = 1
        self.enabled = True
        self.tip = None
        self.tip_job = None
        self.canvas = tk.Canvas(self, bg='#192337', highlightthickness=1, highlightbackground='#344155', height=180, takefocus=True)
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.content = tk.Frame(self.canvas, bg='#192337')
        self.window = self.canvas.create_window(0, 0, anchor='nw', window=self.content)
        self.canvas.bind('<Configure>', self.resize)
        self.content.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.content.bind('<MouseWheel>', self.wheel)
        for key in ('Left', 'Right', 'Up', 'Down', 'Home', 'End', 'space'):
            self.canvas.bind('<' + key + '>', lambda event, key=key: self.key(event, key))
        self.bind('<Destroy>', self.cleanup, add='+')

    def selected_names(self):
        return tuple(name for name in self.names if name in self.selected)

    def set_entries(self, entries):
        """Refresh visible icons; hidden selections cannot affect roster actions."""
        self.hide_tip()
        self.entries = {entry['name']: entry for entry in entries}
        self.names = list(self.entries)
        self.selected.intersection_update(self.names)
        if self.anchor not in self.entries:
            self.anchor = None
        if self.focused not in self.entries:
            self.focused = None
        for tile in self.tiles.values():
            tile.destroy()
        self.tiles.clear()
        for name, entry in self.entries.items():
            tile = tk.Frame(self.content, bg='#192337', width=76, height=88, highlightthickness=2, cursor='hand2')
            tile.pack_propagate(False)
            portrait = tk.Label(tile, image=self.artwork.portraits[name], bg='#192337', borderwidth=0)
            portrait.pack(pady=(3, 0))
            badges = tk.Label(tile, text=(entry['flags'] + '  ' if entry['flags'] else '') + f"{entry['stars']}★", bg='#192337', fg='#fca5a5' if 'X' in entry['flags'] else '#e5e7eb', font=('Segoe UI', 9))
            badges.pack()
            gold = tk.Label(tile, text=f"{entry['cost']}g", bg='#111827', fg=COST_COLORS[entry['cost']], font=('Segoe UI', 8, 'bold'), borderwidth=0)
            gold.place(relx=1, x=-3, y=2, anchor='ne')
            for widget in (tile, portrait, badges, gold):
                widget.bind('<Button-1>', lambda event, name=name: self.click(event, name))
                widget.bind('<Enter>', lambda event, name=name: self.queue_tip(event, name))
                widget.bind('<Leave>', lambda _: self.hide_tip())
                widget.bind('<MouseWheel>', self.wheel)
            self.tiles[name] = tile
        self.reflow(max(self.canvas.winfo_width(), self.TILE_WIDTH))
        self.paint_selection()
        self.notify()

    def reflow(self, width):
        self.columns = max(1, (width - 2) // self.TILE_WIDTH)
        for index, name in enumerate(self.names):
            self.tiles[name].grid(row=index // self.columns, column=index % self.columns, padx=4, pady=4, sticky='nw')

    def resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width - 2)
        if max(1, (event.width - 2) // self.TILE_WIDTH) != self.columns:
            self.reflow(event.width)
        self.hide_tip()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.hide_tip()

    def click(self, event, name):
        if not self.enabled:
            return 'break'
        self.canvas.focus_set()
        self.hide_tip()
        self.choose(name, control=bool(event.state & 0x4), shift=bool(event.state & 0x1))
        return 'break'

    def choose(self, name, control=False, shift=False):
        if shift and self.anchor in self.entries:
            start, end = sorted((self.names.index(self.anchor), self.names.index(name)))
            interval = set(self.names[start:end + 1])
            self.selected = self.selected | interval if control else interval
        elif control:
            self.selected.symmetric_difference_update({name})
            self.anchor = name
        else:
            self.selected = {name}
            self.anchor = name
        self.focused = name
        self.paint_selection()
        self.notify()

    def paint_selection(self):
        for name, tile in self.tiles.items():
            tile.configure(highlightbackground='#c4b5fd' if name in self.selected else '#344155', highlightcolor='#c4b5fd')

    def notify(self):
        chosen = self.focused if self.focused in self.selected else next(iter(self.selected_names()), None)
        self.on_select(chosen)

    def key(self, event, key):
        if not self.enabled or not self.names:
            return 'break'
        index = self.names.index(self.focused) if self.focused in self.entries else 0
        if key == 'space':
            name = self.names[index]
            self.choose(name, control=True, shift=bool(event.state & 0x1))
        else:
            delta = {'Left': -1, 'Right': 1, 'Up': -self.columns, 'Down': self.columns}
            target = 0 if key == 'Home' else len(self.names) - 1 if key == 'End' else max(0, min(len(self.names) - 1, index + delta[key]))
            self.choose(self.names[target], shift=bool(event.state & 0x1))
            self.see(target)
        return 'break'

    def see(self, index):
        top = (index // self.columns) * self.TILE_HEIGHT
        total = max(self.content.winfo_height(), 1)
        visible_top = self.canvas.canvasy(0)
        visible_bottom = visible_top + self.canvas.winfo_height()
        if top < visible_top:
            self.canvas.yview_moveto(top / total)
        elif top + self.TILE_HEIGHT > visible_bottom:
            self.canvas.yview_moveto((top + self.TILE_HEIGHT - self.canvas.winfo_height()) / total)

    def wheel(self, event):
        self.hide_tip()
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def queue_tip(self, event, name):
        self.hide_tip()
        self.tip_job = self.after(400, lambda: self.show_tip(name, event.x_root, event.y_root))

    def show_tip(self, name, x, y):
        self.tip_job = None
        if name not in self.entries or not self.enabled:
            return
        entry = self.entries[name]
        self.tip = tk.Toplevel(self)
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        text = f"{name}  ·  {entry['cost']}g  ·  {entry['stars']}★\n" + ', '.join(entry['traits'])
        if entry['unlockable']:
            text += '\nUnlock required'
        tk.Label(self.tip, text=text, justify='left', wraplength=280, bg='#253047', fg='#ffffff', padx=10, pady=7, font=('Segoe UI', 10)).pack()
        self.tip.update_idletasks()
        x = max(0, min(x + 14, self.winfo_screenwidth() - self.tip.winfo_reqwidth()))
        y = max(0, min(y + 18, self.winfo_screenheight() - self.tip.winfo_reqheight()))
        self.tip.geometry(f'+{x}+{y}')

    def hide_tip(self):
        if self.tip_job is not None:
            self.after_cancel(self.tip_job)
            self.tip_job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def cleanup(self, event):
        if event.widget is self:
            self.hide_tip()
