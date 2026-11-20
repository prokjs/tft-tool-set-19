"""Load bundled Set 19 artwork once; no runtime network or Pillow dependency."""
import json
from pathlib import Path
import tkinter as tk

COST_COLORS = {1: '#9ca3af', 2: '#4ade80', 3: '#60a5fa', 4: '#c084fc', 5: '#fbbf24', 7: '#fb7185'}


class Artwork:
    def __init__(self, root: tk.Misc, directory: Path):
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self.champions = {}
        self.portraits = {}
        self.traits = {}
        for name, asset in manifest['champions'].items():
            self.champions[name] = tk.PhotoImage(master=root, file=str(directory / asset['small']))
            self.portraits[name] = tk.PhotoImage(master=root, file=str(directory / asset['large']))
        for name, asset in manifest['traits'].items():
            self.traits[name] = tk.PhotoImage(master=root, file=str(directory / asset['file']))
