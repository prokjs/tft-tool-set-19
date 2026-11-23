"""Stable portrait roster with a continuous, non-overlapping two-axis fisheye."""
import math
import tkinter as tk
from tkinter import ttk
from visuals import Portrait, COST_COLORS
from fisheye import axis, inverse_axis
from champion_info import unlock_text, star_text
from localization import localize_tree, search_text, tr


class ChampionGrid(tk.Frame):
    # Smaller viewports may scroll, but a baseline portrait stays at least 12px.
    # The fisheye deliberately compresses far context below that baseline.
    MIN_CELL = 14

    def __init__(self, parent, artwork, on_select, on_activate):
        super().__init__(parent, bg='#192337')
        self.artwork = artwork
        self.on_select = on_select
        self.on_activate = on_activate
        self.entries = {}
        self.names = []
        self.tiles = {}
        self.selected = set()
        self.anchor = None
        self.focused = None
        self.columns = 1
        self.rows = 1
        self.enabled = True
        self.query = ''
        self.search_terms = {}
        self.cell_bounds = {}
        self.fisheye_focus = None
        self._pending_focus = None
        self._projection_job = None
        self._leave_job = None
        self._image_cache = {}
        self._grid_width = self.MIN_CELL
        self._grid_height = self.MIN_CELL
        self._pointer_position = None
        self._blocked_pointer = None
        self._pointer_focus = False
        self.lens_images = {}
        self.tip = None
        self.tip_name = None
        self.canvas = tk.Canvas(self, bg='#192337', highlightthickness=1,
                                highlightbackground='#344155', height=336, takefocus=True)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.scroll)
        self.canvas.configure(yscrollcommand=self.scrolled)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.scrollbar.pack(side='right', fill='y')
        self.content = tk.Frame(self.canvas, bg='#192337')
        self.window = self.canvas.create_window(1, 1, anchor='nw', window=self.content)
        self.canvas.bind('<Configure>', self.resize)
        self.bind_pointer(self.canvas)
        self.bind_pointer(self.content)
        self.canvas.bind('<FocusIn>', self.focus_in)
        self.canvas.bind('<FocusOut>', lambda _: self.reset_fisheye())
        self.canvas.bind('<Escape>', lambda _: self.reset_fisheye())
        self.canvas.bind('<F1>', lambda _: self.show_tip(self.focused))
        for key in ('Left', 'Right', 'Up', 'Down', 'Home', 'End', 'space', 'Return'):
            self.canvas.bind('<' + key + '>', lambda event, key=key: self.key(event, key))
        self._top = self.winfo_toplevel()
        self._resize_binding = self._top.bind('<Configure>', self.window_changed, add='+')
        self.bind('<Destroy>', self.cleanup, add='+')

    def selected_names(self):
        return tuple(name for name in self.names if name in self.selected)

    def clear_selection(self):
        self.reset_fisheye()
        self.selected.clear()
        self.anchor = self.focused = None
        self.paint_selection()
        self.notify()

    def set_entries(self, entries):
        """Only pool eligibility rebuilds tiles; projection and search never do."""
        self.reset_fisheye()
        previous_names = self.names
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
        self.search_terms.clear()
        for name, entry in self.entries.items():
            tile = tk.Frame(self.content, bg='#192337', highlightthickness=0, cursor='hand2')
            portrait = Portrait(tile, self.artwork.champions[name], entry['cost'], entry['stars'])
            portrait.configure(highlightthickness=1, highlightbackground=COST_COLORS[entry['cost']])
            badge_text = ''.join(entry['flags'].split())
            if entry.get('slots', 1) > 1:
                badge_text += f"{entry['slots']}s"
            badges = tk.Label(tile, text=badge_text, bg='#192337',
                              fg='#fca5a5' if 'X' in entry['flags'] else '#e5e7eb',
                              font=('Segoe UI', -8, 'bold'), borderwidth=0, padx=0, pady=0)
            badges._preserve_text = True
            tile.portrait = portrait
            tile.badges = badges
            tile._layout = None
            for widget in (tile, portrait, badges):
                self.bind_pointer(widget)
            self.tiles[name] = tile
            terms = [name, tr(name), *entry['traits'], *(tr(trait) for trait in entry['traits'])]
            self.search_terms[name] = search_text(' '.join(terms))
        self.set_search(self.query)
        self.reflow(self.canvas.winfo_width(), self.canvas.winfo_height())
        if self.names != previous_names:
            self.canvas.yview_moveto(0)
        self.paint_selection()
        self.notify()

    def set_search(self, query):
        self.query = query
        needle = search_text(query.strip())
        for name, tile in self.tiles.items():
            match = not needle or needle in self.search_terms[name]
            self.entries[name]['search_match'] = match
            tile.portrait.set_dimmed(not match)
            tile.badges.configure(fg=('#fca5a5' if 'X' in self.entries[name]['flags'] else '#e5e7eb')
                                 if match else '#64748b')

    def reflow(self, width, height=None):
        """Choose the best-fitting base grid, scrolling only below the size floor."""
        width = max(self.MIN_CELL, width - 2)
        height = max(1, (self.canvas.winfo_height() if height is None else height) - 2)
        count = max(1, len(self.names))
        max_columns = min(count, max(1, width // self.MIN_CELL))
        self.columns = max(range(1, max_columns + 1),
                           key=lambda cols: min(width / cols, height / math.ceil(count / cols)))
        self.rows = math.ceil(count / self.columns)
        self._grid_width = width
        self._grid_height = max(height, self.rows * self.MIN_CELL)
        self.canvas.itemconfigure(self.window, width=width, height=self._grid_height)
        self.canvas.configure(scrollregion=(0, 0, width + 2, self._grid_height + 2))
        self.project(None)

    def resized_image(self, name, size):
        # Quantize larger icons; tiny context needs single-pixel size steps.
        size = max(1, int(size) // 4 * 4) if size >= 12 else max(1, int(size))
        key = (name, size)
        if key not in self._image_cache:
            original = (self.artwork.portraits if size > 32 else self.artwork.champions)[name]
            if original.width() == size and original.height() == size:
                image = original
            else:
                gx = math.gcd(size, original.width())
                gy = math.gcd(size, original.height())
                # Tk's combined copy subsamples before zooming, destroying
                # detail at rational sizes. Enlarge first, then sample down.
                expanded = original.zoom(size // gx, size // gy)
                image = expanded.subsample(original.width() // gx, original.height() // gy)
            self._image_cache[key] = image
        return self._image_cache[key]

    def project(self, focus):
        self.fisheye_focus = focus
        width, height = self._grid_width, self._grid_height
        xs = [index * width / self.columns for index in range(self.columns + 1)]
        ys = [index * height / self.rows for index in range(self.rows + 1)]
        if focus is not None:
            xs = [axis(value, focus[0], width) for value in xs]
            ys = [axis(value, focus[1], height) for value in ys]
        xs, ys = [round(value) for value in xs], [round(value) for value in ys]
        self.cell_bounds.clear()
        for index, name in enumerate(self.names):
            row, col = divmod(index, self.columns)
            x, y = xs[col], ys[row]
            cell_width, cell_height = xs[col + 1] - x, ys[row + 1] - y
            self.cell_bounds[name] = (x, y, cell_width, cell_height)
            tile = self.tiles[name]
            tile.place(x=x, y=y, width=cell_width, height=cell_height)
            size = max(1, min(cell_width, cell_height) - 2)
            image = self.resized_image(name, size)
            layout = (cell_width, cell_height, image.width())
            if tile._layout != layout:
                tile.portrait.set_image(image)
                tile.portrait.place(x=(cell_width - image.width()) // 2 - 1,
                                    y=(cell_height - image.height()) // 2 - 1)
                if min(cell_width, cell_height) >= 28 and tile.badges.cget('text'):
                    tile.badges.place(x=0, y=cell_height - 12, width=cell_width - 2, height=10)
                else:
                    tile.badges.place_forget()
                tile._layout = layout

    def schedule_projection(self, focus):
        self._pending_focus = focus
        if self._projection_job is None:
            self._projection_job = self.after(16, self.apply_projection)

    def apply_projection(self):
        self._projection_job = None
        if self._pending_focus != self.fisheye_focus:
            self.project(self._pending_focus)

    def reset_fisheye(self):
        self._blocked_pointer = self._pointer_position
        self.hide_tip()
        if self._projection_job is not None:
            self.after_cancel(self._projection_job)
            self._projection_job = None
        self._pending_focus = None
        if self.fisheye_focus is not None:
            self.project(None)

    def resize(self, event):
        self.reset_fisheye()
        self.reflow(event.width, event.height)

    def window_changed(self, event):
        if event.widget is self._top:
            self.reset_fisheye()

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.reset_fisheye()

    def click(self, event, name):
        if not self.enabled:
            return 'break'
        self._pointer_focus = self.focus_get() is not self.canvas
        self.canvas.focus_set()
        self.hide_tip()
        if self.on_activate(name):
            if name in self.entries:
                self.choose(name)
        else:
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
            color = '#c4b5fd' if name in self.selected or self.entries[name].get('pool_member') else COST_COLORS[self.entries[name]['cost']]
            tile.portrait.configure(highlightbackground=color, highlightcolor=color)

    def notify(self):
        chosen = self.focused if self.focused in self.selected else next(iter(self.selected_names()), None)
        self.on_select(chosen)

    def key(self, event, key):
        if not self.enabled or not self.names:
            return 'break'
        self.hide_tip()
        index = self.names.index(self.focused) if self.focused in self.entries else 0
        if key in ('space', 'Return'):
            name = self.names[index]
            if not self.on_activate(name):
                self.choose(name, control=(key == 'space'), shift=bool(event.state & 0x1))
            elif name in self.entries:
                self.choose(name)
        else:
            delta = {'Left': -1, 'Right': 1, 'Up': -self.columns, 'Down': self.columns}
            target = 0 if key == 'Home' else len(self.names) - 1 if key == 'End' else max(0, min(len(self.names) - 1, index + delta[key]))
            self.choose(self.names[target], control=bool(event.state & 0x4), shift=bool(event.state & 0x1))
            self.see(target)
        self.show_focused_tip()
        return 'break'

    def see(self, index):
        top = index // self.columns * self._grid_height / self.rows
        cell_height = self._grid_height / self.rows
        visible_top = self.canvas.canvasy(1) - 1
        visible_height = self.canvas.winfo_height() - 2
        if top < visible_top:
            self.canvas.yview_moveto(top / (self._grid_height + 2))
        elif top + cell_height > visible_top + visible_height:
            self.canvas.yview_moveto((top + cell_height - visible_height) / (self._grid_height + 2))

    def scroll(self, *args):
        self.reset_fisheye()
        self.canvas.yview(*args)

    def scrolled(self, first, last):
        self.scrollbar.set(first, last)

    def wheel(self, event):
        self.reset_fisheye()
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def focus_in(self, event):
        if self._pointer_focus:
            self._pointer_focus = False
        else:
            self.show_focused_tip()

    def bind_pointer(self, widget):
        widget.bind('<Button-1>', self.pointer_click)
        widget.bind('<Button-3>', self.detail_click)
        widget.bind('<Motion>', self.queue_tip)
        widget.bind('<Leave>', self.leave_tip)
        widget.bind('<MouseWheel>', self.wheel)

    def local_pointer(self, x_root, y_root):
        x = x_root - self.canvas.winfo_rootx()
        y = y_root - self.canvas.winfo_rooty()
        if not (1 <= x < self.canvas.winfo_width() - 1
                and 1 <= y < self.canvas.winfo_height() - 1):
            return None
        return self.canvas.canvasx(x) - 1, self.canvas.canvasy(y) - 1

    def pointer_name(self, x_root, y_root):
        """Hit-test the displayed rectangles, including their rounded boundaries."""
        point = self.local_pointer(x_root, y_root)
        if point is None:
            return None
        x, y = point
        if not (0 <= x < self._grid_width and 0 <= y < self._grid_height):
            return None
        base_x, base_y = point
        if self.fisheye_focus is not None:
            fx, fy = self.fisheye_focus
            base_x = inverse_axis(x, fx, self._grid_width)
            base_y = inverse_axis(y, fy, self._grid_height)
        col = min(self.columns - 1, int(base_x * self.columns / self._grid_width))
        row = min(self.rows - 1, int(base_y * self.rows / self._grid_height))
        index = row * self.columns + col
        candidates = self.names[index:index + 1]
        if candidates:
            name = candidates[0]
            left, top, width, height = self.cell_bounds[name]
            if left <= x < left + width and top <= y < top + height:
                return name
        # Integer placement can move a boundary by half a pixel relative to
        # the continuous inverse. The visible rectangle is always decisive.
        for name, (left, top, width, height) in self.cell_bounds.items():
            if left <= x < left + width and top <= y < top + height:
                return name
        return None

    def pointer_click(self, event):
        name = self.pointer_name(event.x_root, event.y_root)
        if name is not None:
            return self.click(event, name)
        self.hide_tip()
        return 'break'

    def detail_click(self, event):
        name = self.pointer_name(event.x_root, event.y_root)
        if name is not None:
            self.show_tip(name)
        return 'break'

    def queue_tip(self, event):
        self.move_pointer(event.x_root, event.y_root)

    def move_pointer(self, x_root, y_root):
        self._pointer_position = (x_root, y_root)
        if not self.enabled or self._pointer_position == self._blocked_pointer:
            return
        self._blocked_pointer = None
        point = self.local_pointer(x_root, y_root)
        if point is None:
            self.reset_fisheye()
        else:
            self.hide_tip()
            self.schedule_projection(point)

    def leave_tip(self, event=None):
        # Child Leave events are common while tiles move. Only leaving the
        # container resets the lens; entering a different tile is irrelevant.
        if self._leave_job is None:
            self._leave_job = self.after_idle(self.finish_leave)

    def finish_leave(self):
        self._leave_job = None
        if self.local_pointer(*self.winfo_pointerxy()) is None:
            self.reset_fisheye()

    def show_focused_tip(self):
        if not self.enabled:
            return
        if self.names and self.focused not in self.entries:
            self.focused = self.names[0]
        if self.focused in self.entries:
            self._blocked_pointer = self._pointer_position
            index = self.names.index(self.focused)
            row, col = divmod(index, self.columns)
            focus = ((col + 0.5) * self._grid_width / self.columns,
                     (row + 0.5) * self._grid_height / self.rows)
            if self._projection_job is not None:
                self.after_cancel(self._projection_job)
                self._projection_job = None
            self._pending_focus = focus
            self.project(focus)

    def new_tip(self):
        self.hide_tip()
        tip = tk.Toplevel(self, takefocus=False, bg='#253047')
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes('-topmost', True)
        tip.bind('<Enter>', lambda _: self.hide_tip())
        return tip

    def place_tip(self, tip, target):
        """Details are secondary and never cover another roster hit target."""
        localize_tree(tip)
        tip.update_idletasks()
        width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        left, top = self.winfo_rootx(), self.winfo_rooty()
        right, bottom = left + self.winfo_width(), top + self.winfo_height()
        gap = 8
        y = max(0, min(target.winfo_rooty(), screen_height - height))
        positions = [(right + gap, y), (left - gap - width, y),
                     (max(0, min(target.winfo_rootx(), screen_width - width)), top - gap - height),
                     (max(0, min(target.winfo_rootx(), screen_width - width)), bottom + gap)]
        for x, y in positions:
            if x >= 0 and y >= 0 and x + width <= screen_width and y + height <= screen_height:
                tip.geometry(f'+{x}+{y}')
                self.tip = tip
                tip.deiconify()
                return True
        tip.destroy()
        return False

    def show_tip(self, name, x=None, y=None):
        if name not in self.entries or not self.enabled or self.tip_name == name:
            return
        entry = self.entries[name]
        if name not in self.lens_images:
            self.lens_images[name] = self.artwork.portraits[name].zoom(2, 2)
        tip = self.new_tip()
        portrait = Portrait(tip, self.lens_images[name], entry['cost'], entry['stars'], bg='#253047')
        portrait.pack(side='left', anchor='n', padx=(8, 0), pady=8)
        text = f"{name}  ·  {entry['cost']}g  ·  planned {star_text(entry['stars'])}\n" + ', '.join(entry['traits'])
        if entry.get('role'):
            text += '\nRole: ' + entry['role']
        markers = [label for flag, label in (
            ('C', 'C = Collab'), ('L', 'L = team locked'), ('X', 'X = excluded'))
            if flag in entry['flags']]
        if entry.get('fixed'):
            markers.append('Sona is automatic.')
        if markers:
            text += '\n' + ' · '.join(markers)
        if entry.get('slots', 1) > 1:
            text += f"\nOccupies {entry['slots']} team slots."
        text += '\n' + unlock_text(entry)
        tk.Label(tip, text=text, justify='left', wraplength=280, bg='#253047', fg='#ffffff',
                 padx=10, pady=7, font=('Segoe UI', 10)).pack(side='left', anchor='n')
        if self.place_tip(tip, self.tiles[name]):
            self.tip_name = name

    def show_help(self, event, text):
        tip = self.new_tip()
        tk.Label(tip, text=text, justify='left', wraplength=340, bg='#253047', fg='#ffffff',
                 padx=10, pady=7, font=('Segoe UI', 10)).pack()
        self.place_tip(tip, event.widget)

    def hide_tip(self):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        self.tip_name = None

    def cleanup(self, event):
        if event.widget is self:
            self.hide_tip()
            for job in (self._projection_job, self._leave_job):
                if job is not None:
                    self.after_cancel(job)
            self._top.unbind('<Configure>', self._resize_binding)
