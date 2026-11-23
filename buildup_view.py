"""Scrollable, portrait-based display of precomputed conditional leveling plans."""
import tkinter as tk
from tkinter import ttk
import webbrowser

from buildup import format_buildup
from trait_rows import TraitRow, STYLE_ORDER, badge_style
from visuals import COST_COLORS, Portrait
from champion_info import star_text, trait_members
from team_guidance import item_profile
from localization import tr, localize_tree
from board_view import BoardView, ChampionTip, champion_details


BG = '#111827'
PANEL = '#192337'
TEXT = '#e5e7eb'
MUTED = '#9ca3af'
ACCENT = '#c4b5fd'
WARNING = '#f1d69b'


class BuildupView(tk.Frame):
    """Embedded guide; ``show_comp`` refreshes a precomputed result.

    ``on_temporary_choice(level, slot, champion)`` asks the app to replan.
    Navigation and rendering never invoke the optimizer.
    """

    def __init__(self, parent, data, artwork, on_unlock, on_temporary_choice):
        super().__init__(parent, bg=BG, takefocus=True)
        self.result = None
        self.data = data
        self.collab_pool = []
        self.sona_band = ''
        self.artwork = artwork
        self.on_unlock = on_unlock
        self.on_temporary_choice = on_temporary_choice
        self.champions = {champion['name']: champion for champion in data['champions']}
        self.definitions = {trait['name']: trait for trait in data['traits']}
        self.steps = []
        self.target = set()
        self.index = 0
        self.selected_level = None
        self.selected_step = None
        self.selected_phase = 0
        self.selected_board = None
        self.wrap_labels = []
        self.level_buttons = []
        self.choice_buttons = []
        self.busy = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = tk.Frame(self, bg=BG, padx=10, pady=6)
        header.grid(row=0, column=0, sticky='ew')
        heading = tk.Frame(header, bg=BG)
        heading.pack(fill='x')
        self.level_title = tk.Label(heading, bg=BG, fg=ACCENT,
                                    font=('Segoe UI', 13, 'bold'), anchor='w')
        self.level_title.pack(side='left', fill='x', expand=True)
        self.previous_button = ttk.Button(heading, text='← Previous', command=lambda: self.move(-1))
        self.previous_button.pack(side='left', padx=4)
        self.next_button = ttk.Button(heading, text='Next →', command=lambda: self.move(1))
        self.next_button.pack(side='left')
        tk.Label(header, text='Leveling guide · select a level to inspect its setup and arrival',
                 bg=BG, fg=MUTED, anchor='w', font=('Segoe UI', 9)).pack(fill='x', pady=(3, 7))
        self.overview = tk.Frame(header, bg=BG)
        self.overview.pack(fill='x')
        self.overview.bind('<Configure>', self._overview_resize)
        self.phase_controls = tk.Frame(header, bg=BG)
        tk.Label(self.phase_controls, text='Final pivot:', bg=BG, fg=ACCENT,
                 font=('Segoe UI', 9, 'bold')).pack(side='left', padx=(0, 8))
        self.phase_previous = ttk.Button(self.phase_controls, text='← Phase', width=9,
                                         command=lambda: self.select_phase(self.selected_phase - 1))
        self.phase_previous.pack(side='left')
        self.phase_selector = ttk.Combobox(self.phase_controls, state='readonly', width=24)
        self.phase_selector.pack(side='left', fill='x', expand=True, padx=6)
        self.phase_selector.bind('<<ComboboxSelected>>',
                                 lambda _event: self.select_phase(self.phase_selector.current()))
        self.phase_next = ttk.Button(self.phase_controls, text='Phase →', width=9,
                                     command=lambda: self.select_phase(self.selected_phase + 1))
        self.phase_next.pack(side='left')

        scroll_frame = tk.Frame(self, bg=BG)
        scroll_frame.grid(row=1, column=0, sticky='nsew', padx=12)
        self.canvas = tk.Canvas(scroll_frame, bg=BG, highlightthickness=0, yscrollincrement=24)
        scrollbar = ttk.Scrollbar(scroll_frame, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.content = tk.Frame(self.canvas, bg=BG)
        self.content_window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._canvas_resize)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Button-4>', self._wheel)
        self.canvas.bind('<Button-5>', self._wheel)
        self.canvas.bind('<Button-1>', lambda _event: self.canvas.focus_set(), add='+')
        for widget in (self, self.canvas, self.previous_button, self.next_button):
            widget.bind('<Left>', lambda _event: self.move(-1))
            widget.bind('<Right>', lambda _event: self.move(1))
            widget.bind('<Home>', lambda _event: self.select_level(self.steps[0]['level']) if self.steps else 'break')
            widget.bind('<End>', lambda _event: self.select_level(self.steps[-1]['level']) if self.steps else 'break')
            widget.bind('<Control-c>', lambda _event: self.copy_buildup())

        footer = tk.Frame(self, bg=BG, padx=10, pady=6)
        footer.grid(row=2, column=0, sticky='ew')
        self.copy_button = ttk.Button(footer, text='Copy complete build-up', command=self.copy_buildup)
        self.copy_button.pack(side='left')
        self.copy_status = tk.Label(footer, text='', bg=BG, fg=MUTED, font=('Segoe UI', 9))
        self.copy_status.pack(side='left', padx=10)
        self.show_message('Select a suggested team to see every level of its guide here.')

    def show_comp(self, result, retain_level=False):
        """Replace the guide, optionally retaining its level; always show Arrival."""
        previous_level = self.selected_level if retain_level else None
        self.result = result
        self.collab_pool = result['collab_pool']
        self.sona_band = result['sona_band']
        self.steps = sorted(result['buildup'], key=lambda step: step['level'])
        self.target = set(result['champions'])
        self.selected_step = None
        for button in self.level_buttons:
            button.destroy()
        self.level_buttons.clear()
        self.copy_status.configure(text='')
        for step in self.steps:
            board = step['board']
            slots = sum(self.champions[name].get('slots', 1) for name in board['champions']) if board else 0
            quests = len(step['unlocks'])
            detail = f'{quests} conditional' if quests else 'No unlock needed'
            if any(preparation.get('board') for preparation in step.get('preparation_boards', [])):
                detail = 'UNLOCK PREPARATION' + (' · ' + detail if quests else '')
            button = tk.Button(self.overview,
                               text=f"L{step['level']} · {slots}/{step['level']} slots\n{detail}",
                               command=lambda level=step['level']: self.select_level(level),
                               bg=PANEL, fg=TEXT, activebackground='#4c3b78', activeforeground='white',
                               font=('Segoe UI', 9), relief='flat', borderwidth=1, padx=7, pady=4,
                               highlightthickness=1, highlightbackground='#344155', cursor='hand2', wraplength=100)
            self.level_buttons.append(button)
        self._overview_resize(None)
        levels = [step['level'] for step in self.steps]
        initial = previous_level if previous_level in levels else 3 if 3 in levels else levels[0] if levels else 0
        self.select_level(initial)
        self.set_busy(self.busy)

    def show_message(self, text):
        """Clear stale plans when the app's constraints or results change."""
        self.result = None
        self.steps = []
        self.selected_level = None
        self.selected_step = None
        self.selected_board = None
        self.selected_phase = 0
        for button in self.level_buttons:
            button.destroy()
        self.level_buttons.clear()
        self.phase_controls.pack_forget()
        self._clear_content()
        self.level_title.configure(text='Leveling guide')
        self.copy_status.configure(text='')
        self._text(self.content, text, MUTED)
        self.canvas.yview_moveto(0)
        self.set_busy(self.busy)
        localize_tree(self)

    def set_busy(self, busy):
        """Disable stale choices and navigation while a worker is recomputing."""
        self.busy = busy
        for button in self.level_buttons + self.choice_buttons:
            button.configure(state='disabled' if busy else 'normal')
        self.previous_button.configure(state='normal' if not busy and self.steps and self.index else 'disabled')
        self.next_button.configure(state='normal' if not busy and self.steps and self.index < len(self.steps) - 1 else 'disabled')
        self.copy_button.configure(state='normal' if not busy and self.result else 'disabled')
        self.phase_selector.configure(state='disabled' if busy else 'readonly')
        self.phase_previous.configure(state='normal' if not busy and self.selected_phase else 'disabled')
        upgrades = self.selected_step.get('upgrades', []) if self.selected_step else []
        self.phase_next.configure(state='normal' if not busy and self.selected_phase < len(upgrades) else 'disabled')

    def _clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()
        self.wrap_labels.clear()
        self.choice_buttons.clear()

    def _overview_resize(self, event):
        columns = max(1, (event.width if event else self.overview.winfo_width()) // 110)
        for index, button in enumerate(self.level_buttons):
            button.grid(row=index // columns, column=index % columns, sticky='ew', padx=3, pady=3)
        for column in range(len(self.level_buttons)):
            self.overview.columnconfigure(column, weight=1 if column < columns else 0)

    def _canvas_resize(self, event):
        self.canvas.itemconfigure(self.content_window, width=max(1, event.width))
        for label in self.wrap_labels:
            label.configure(wraplength=max(120, event.width - 62))

    def _wheel(self, event):
        if getattr(event, 'num', None) == 4:
            amount = -1
        elif getattr(event, 'num', None) == 5:
            amount = 1
        else:
            delta = getattr(event, 'delta', 0)
            if not delta:
                return 'break'
            amount = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
        self.canvas.yview_scroll(amount, 'units')
        return 'break'

    def _bind_wheel(self, widget):
        if isinstance(widget, TraitRow):
            widget.on_wheel = self._wheel
            handler = widget.wheel
        else:
            handler = self._wheel
        widget.bind('<MouseWheel>', handler)
        widget.bind('<Button-4>', handler)
        widget.bind('<Button-5>', handler)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _text(self, parent, text, color=TEXT, bold=False, pady=(2, 4)):
        label = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                         font=('Segoe UI', 10, 'bold' if bold else 'normal'),
                         justify='left', anchor='w', wraplength=max(120, self.canvas.winfo_width() - 62))
        label.pack(fill='x', pady=pady)
        self.wrap_labels.append(label)
        return label

    def _section(self, title):
        section = tk.Frame(self.content, bg=PANEL, padx=12, pady=10)
        section.pack(fill='x', padx=4, pady=(0, 10))
        self._text(section, title, ACCENT, bold=True)
        return section

    @staticmethod
    def _reflow_grid(frame, widgets, tile_width):
        def reflow(event):
            columns = max(1, event.width // tile_width)
            for index, widget in enumerate(widgets):
                widget.grid(row=index // columns, column=index % columns, sticky='nw', padx=4, pady=4)
        frame.bind('<Configure>', reflow)
        for index, widget in enumerate(widgets):
            widget.grid(row=index, column=0, sticky='nw', padx=4, pady=4)

    def move(self, delta):
        if not self.steps:
            return 'break'
        index = max(0, min(len(self.steps) - 1, self.index + delta))
        return self.select_level(self.steps[index]['level'])

    def select_level(self, level: int):
        """Select an actual player level, not a zero-based step index."""
        if self.busy:
            return 'break'
        if not self.steps:
            self.level_title.configure(text='No level plan available')
            self.previous_button.configure(state='disabled')
            self.next_button.configure(state='disabled')
            self._clear_content()
            self._text(self.content, 'This result contains no precomputed level steps. No boards can be displayed.', WARNING)
            return 'break'
        index = next((index for index, step in enumerate(self.steps) if step['level'] == level), None)
        if index is None:
            raise ValueError(f'Level {level} is not present in this build-up.')
        self.index = index
        step = self.steps[index]
        self.selected_level = step['level']
        self.selected_step = step
        self.previous_button.configure(state='normal' if index else 'disabled')
        self.next_button.configure(state='normal' if index < len(self.steps) - 1 else 'disabled')
        for button_index, button in enumerate(self.level_buttons):
            button.configure(bg='#5b3c91' if button_index == index else PANEL,
                             highlightbackground=ACCENT if button_index == index else '#344155',
                             relief='sunken' if button_index == index else 'flat')
        upgrades = step.get('upgrades', [])
        if upgrades:
            self.phase_selector.configure(values=['Arrival · hold lineup'] + [
                f"Upgrade {phase}/{len(upgrades)}" + (' · ideal end goal' if phase == len(upgrades) else '')
                for phase in range(1, len(upgrades) + 1)
            ])
            self.phase_controls.pack(fill='x', pady=(8, 0))
        else:
            self.phase_controls.pack_forget()
        return self.select_phase(0)

    def select_phase(self, index: int):
        """Select 0 for Arrival, or 1..N for precomputed same-level upgrades."""
        if self.busy or self.selected_step is None:
            return 'break'
        step = self.selected_step
        upgrades = step.get('upgrades', [])
        if not 0 <= index <= len(upgrades):
            raise ValueError(f'Phase {index} is not present at level {step["level"]}.')
        self.selected_phase = index
        phase = step if index == 0 else upgrades[index - 1]
        self.selected_board = phase['board']
        context = dict(phase, level=step['level'])
        if upgrades:
            self.phase_selector.current(index)
            self.phase_previous.configure(state='normal' if index else 'disabled')
            self.phase_next.configure(state='normal' if index < len(upgrades) else 'disabled')
        label = (f'upgrade {index}/{len(upgrades)}' if index else
                 'final-level arrival' if self.index == len(self.steps) - 1 else 'build-up arrival')
        self.level_title.configure(text=f"Level {step['level']} · {label}")
        self._clear_content()
        self._text(self.content,
                   'Conditional plan: no quest completion is assumed. Stars are upgrade targets, '
                   'not confirmed purchases or guaranteed hits.',
                   WARNING, pady=(6, 10))
        if upgrades:
            self._text(self.content,
                       'Hold the Arrival lineup until the incoming champions are found and their unlock tasks are complete. '
                       'Buy before selling: secure the next phase’s additions before replacing its listed removals. '
                       'These phases do not assert owned units, available gold, or completed unlocks.',
                       WARNING)
        self._temporary_options(step)
        self._preparation_boards(step)
        self._transition_guidance(phase)
        title = ('Arrival board — hold while finding upgrades' if upgrades and index == 0 else
                 f'Upgrade {index}/{len(upgrades)} — conditional board' if index else
                 'Arrival board — conditional on listed unlocks')
        self._board(title, self.selected_board, context, fallback=False)
        transition = self._section('Changes from the previous arrival board' if index == 0 else
                                   'Changes from ' + ('Arrival' if index == 1 else f'Upgrade {index - 1}'))
        for label, key, color in (('Buy / add first', 'added', '#86efac'), ('Then replace / remove', 'removed', '#fca5a5'),
                                  ('Keep', 'retained', TEXT)):
            self._text(transition, f"{label}: {', '.join(phase[key]) or '—'}", color)
        self._text(transition, phase['reason'], MUTED)
        for milestone in phase['milestones']:
            self._text(transition, '• ' + milestone, WARNING)

        self._unlocks(context)
        self._unlock_prep(context)
        goal = step.get('goal_board')
        if goal is not None:
            ideal = self._section('Ideal end goal — distinct from the arrival lineup')
            self._text(ideal, ', '.join(
                f"{name} ({star_text(goal['planned_stars'].get(name, 1))})"
                for name in goal['champions']))
            self._holder_summary(ideal, goal)
            if upgrades:
                self._text(ideal, f'The last of {len(upgrades)} upgrades is this exact selected goal. '
                           'Use the phase selector to inspect each actual lineup; stay on Arrival until ready.',
                           MUTED)
            else:
                self._text(ideal, 'Arrival already matches the selected end goal; purchases, upgrades and unlocks remain conditional.',
                           MUTED)
        if phase['unlocks'] or self.selected_board is None:
            self._board('Playable alternative — when quests are incomplete', step['fallback'], step, fallback=True)
        notes = self._section('Source policy & planning assumptions')
        for note in self.result['buildup_notes']:
            self._text(notes, '• ' + note, MUTED)
        self._text(notes, 'No combat, item, economy, or quest-progress simulation is performed. '
                   'Trait counts alone do not establish that any unlock quest is complete.', WARNING)
        self._bind_wheel(self.content)
        localize_tree(self)
        self.canvas.yview_moveto(0)
        return 'break'

    def _temporary_options(self, step):
        choices = step.get('temporary_options', [])
        if not choices:
            return
        section = self._section('Choose temporary champions — updates the leveling plan')
        self._text(section, 'Click a portrait or name to choose the unit you can actually find. '
                   'The selected end goal stays fixed; later boards, swaps, traits and item holders are recalculated.',
                   MUTED)
        for choice in choices:
            choice_stars = choice['planned_stars']
            self._text(section, 'Temporary slot: ' + choice['slot'] + ' · Current choice: ' + choice['selected'],
                       ACCENT, bold=True)
            options = tk.Frame(section, bg=PANEL)
            options.pack(fill='x')
            tip = ChampionTip(options)
            buttons = []
            for name in choice['options']:
                selected = name == choice['selected']
                background = '#4c3b78' if selected else PANEL
                card = tk.Frame(options, bg=background, padx=5, pady=3,
                                highlightthickness=2 if selected else 1,
                                highlightbackground=ACCENT if selected else '#344155')
                portrait = Portrait(card, self.artwork.portraits[name],
                                    self.champions[name]['cost'], choice_stars[name], bg=background)
                portrait.pack()
                button = tk.Button(
                    card, text=('✓ ' if selected else '') + name,
                    command=lambda level=step['level'], slot=choice['slot'], name=name:
                        self._choose_temporary(level, slot, name),
                    bg=background, fg=TEXT,
                    activebackground='#5b3c91', activeforeground='white',
                    relief='sunken' if selected else 'flat', cursor='hand2',
                    borderwidth=0, highlightthickness=0, wraplength=104,
                    padx=2, pady=2, font=('Segoe UI', 9, 'bold'))
                button.pack(fill='x')
                for widget in (card, portrait):
                    widget.configure(cursor='hand2')
                    widget.bind('<Button-1>', lambda _event, button=button: button.invoke())
                detail = champion_details(self.champions[name], choice_stars[name])
                for widget in (card, portrait, button):
                    tip.bind(widget, detail)
                buttons.append(card)
                self.choice_buttons.append(button)
            self._reflow_grid(options, buttons, 124)

    def _choose_temporary(self, level, slot, name):
        if self.busy or self.result is None:
            return
        choice = next((choice for choice in self.selected_step.get('temporary_options', [])
                       if choice['slot'] == slot), None)
        if choice is None or name == choice['selected'] or name not in choice['options']:
            return
        self.on_temporary_choice(level, slot, name)

    def _preparation_boards(self, step):
        for preparation in sorted(step.get('preparation_boards', []),
                                  key=lambda item: (item.get('board') is None, item['target'])):
            target = preparation['target']
            section = self._section('Unlock setup BEFORE arrival — ' + target)
            tk.Button(section, text=target + ' — sourced unlock details ↗',
                      command=lambda name=target: self.on_unlock(name),
                      bg=PANEL, fg=WARNING, activebackground='#253047', activeforeground='white',
                      relief='flat', cursor='hand2', anchor='w',
                      font=('Segoe UI', 10, 'bold')).pack(anchor='w')
            self._text(section, 'Required task — not confirmed: ' + preparation['condition'], WARNING, bold=True)
            self._text(section, 'Use the actual setup lineup below before the conditional handoff. '
                       'Manually verify items, stars, positions, combat and quest progress in game; '
                       'this plan never marks an unlock complete.', WARNING)
            if preparation.get('note'):
                self._text(section, preparation['note'], WARNING)
            if preparation.get('positioning_requirement'):
                self._text(section, preparation['positioning_requirement'], WARNING, bold=True)
            for dependency in preparation.get('conditional_dependencies', []):
                self._text(section, 'First unlock setup dependency: ' + dependency['name'] +
                           ' — ' + dependency['condition'], WARNING)
            sources = preparation.get('sources') or preparation.get('source') or self.champions[target].get('unlock_source')
            if isinstance(sources, str):
                sources = [sources]
            for source in sources or []:
                self._text(section, 'Source: ' + str(source), MUTED)
            board = preparation['board']
            context = dict(step, stars=board['planned_stars'] if board else {},
                           reason=preparation.get('note') or 'No legal setup lineup is available at this level.',
                           unlocks=[self.champions[name] for name in board['champions']
                                    if self.champions[name]['unlockable']] if board else [],
                           unlock_prep=[])
            self._board('Actual preparation lineup — ' + target, board, context, fallback=False)

    def _transition_guidance(self, phase):
        section = self._section('Exact replacements — secure incoming units before selling')
        replacements = phase.get('replacements', [])
        for replacement in replacements:
            outgoing = ' + '.join(replacement['outgoing'])
            incoming = ' + '.join(replacement['incoming'])
            match = ('same-role · ' + replacement['profile'].upper()
                     if replacement['same_role'] else 'roster-only · no role match')
            self._text(section, f'Replace {outgoing} → {incoming} [{match}]', ACCENT, bold=True)
        if not replacements:
            self._text(section, 'No paired replacement in this phase.', MUTED)
        paired_incoming = {name for replacement in replacements for name in replacement['incoming']}
        additions = [name for name in phase['added'] if name not in paired_incoming]
        if additions:
            self._text(section, 'Open-slot additions: ' + ', '.join(additions), '#86efac', bold=True)
        for transfer in phase.get('item_transfers', []):
            method = ('Sell the outgoing holder to release items.'
                      if transfer['method'] == 'sell_outgoing' else
                      'Old holder stays fielded: requires an item remover or a later sale; no free transfer.')
            self._text(section,
                       f"{transfer['kind'].upper()} items: {transfer['outgoing']} → {transfer['incoming']}. "
                       + method + (' ' + transfer['condition'] if transfer['condition'] else ''),
                       WARNING, bold=True)

    def _holder_summary(self, parent, board):
        holders = board.get('item_holders', {})
        self._text(parent, 'Item-holder options: ' + ' · '.join(
            f"{kind.upper()}: " + (holders.get(kind) or (
                'no dedicated holder' if kind in ('ad', 'ap') and holders.get('flex')
                else 'no suitable holder'))
            for kind in ('tank', 'ad', 'ap', 'flex')), ACCENT, bold=True)
        self._text(parent, 'Choose holders for the items you actually have; these are alternatives, not '
                   'three guaranteed item sets. Flex uses one chosen AD or AP build, never both. '
                   'No item inventory or ownership is simulated.', MUTED)

    def _board(self, title, board, step, fallback):
        section = self._section(title)
        if board is None:
            if fallback:
                self._text(section, 'No playable no-unlock alternative was found at this level.', WARNING, bold=True)
                for milestone in step['milestones']:
                    self._text(section, milestone, WARNING)
            else:
                self._text(section, step['reason'], WARNING, bold=True)
            return
        names = board['champions']
        slots = sum(self.champions[name].get('slots', 1) for name in names)
        self._text(section, f"{slots}/{step['level']} slots · {len(names)} champions · "
                   f"{board['active']} active non-unique traits · {board['cost']}g base unit cost", MUTED)
        self._holder_summary(section, board)
        for build in board.get('planned_item_builds', []):
            if build.get('holder') not in names:
                self._text(section, 'No current holder for ' + build['target'] + ': '
                           + ' · '.join(build['items']), WARNING, bold=True)
                self._item_build_details(section, build)
        if fallback:
            self._text(section, 'Only champions without conditional unlocks. This is an independent alternative, '
                       'not another pivot phase; the Add / Remove / Keep list describes the selected primary board.', MUTED)
        elif not step['unlocks']:
            self._text(section, 'No conditional unlocks are required by the selected board.', MUTED)
        portraits = tk.Frame(section, bg=PANEL)
        portraits.pack(fill='x')
        tip = ChampionTip(portraits)
        cards = []
        temporary = set(names) - self.target
        preparation = {}
        if not fallback:
            for unlock in step.get('unlock_prep', []):
                for helper in unlock['helpers']:
                    preparation.setdefault(helper, []).append(unlock['name'])
        for name in names:
            champion = self.champions[name]
            color = COST_COLORS[champion['cost']]
            card = tk.Frame(portraits, bg=PANEL, highlightbackground=color, highlightthickness=1,
                            width=116, padx=4, pady=3)
            stars = board['planned_stars'].get(name, 1)
            portrait = Portrait(card, self.artwork.portraits[name], champion['cost'], stars)
            portrait.configure(cursor='hand2')
            portrait.bind('<Button-1>', lambda _event, name=name: self.on_unlock(name))
            portrait.pack()
            tk.Label(card, text=name, bg=PANEL, fg=TEXT, width=14, wraplength=104,
                     font=('Segoe UI', 9, 'bold')).pack()
            status = []
            if name in self.collab_pool or name == 'Sona':
                status.append('C')
            if champion.get('slots', 1) > 1:
                status.append(f"{champion['slots']} slots")
            if status:
                tk.Label(card, text=' · '.join(status), bg=PANEL, fg=color,
                         font=('Segoe UI', 8)).pack()
            profile = item_profile(champion)
            role = {'tank': 'Tank', 'ad': 'AD', 'ap': 'AP', 'flex': 'AD / AP flex',
                    'unknown': 'Unclassified'}[profile]
            tk.Label(card, text=role, bg=PANEL, fg=MUTED, font=('Segoe UI', 8)).pack()
            support = board['champion_trait_support'][name]
            percent = support['percent']
            tk.Label(card, text=(f'Trait tiers {percent:g}%' if percent is not None else 'Trait tiers n/a'),
                     bg=PANEL, fg=ACCENT, font=('Segoe UI', 8)).pack()
            holder_kinds = [kind.upper() for kind, holder in board.get('item_holders', {}).items()
                            if holder == name]
            if holder_kinds:
                tag = 'AD OR AP ITEMS' if profile == 'flex' else ' / '.join(holder_kinds) + ' ITEM HOLDER'
                tk.Label(card, text=tag, bg=PANEL, fg=ACCENT, wraplength=104,
                         font=('Segoe UI', 8, 'bold')).pack(pady=2)
            for build in board.get('planned_item_builds', []):
                if build.get('holder') != name:
                    continue
                tk.Label(card, text='For ' + build['target'] if build['target'] != name else 'Final item plan',
                         bg=PANEL, fg=ACCENT, wraplength=104,
                         font=('Segoe UI', 8, 'bold')).pack(pady=(5, 1))
                tk.Label(card, text='\n'.join(build['items']), bg=PANEL, fg=TEXT,
                         wraplength=104, font=('Segoe UI', 8), justify='center').pack()
                self._item_build_details(card, build, wraplength=104)
            tk.Label(card, text='TEMPORARY' if name in temporary else 'FINAL TARGET',
                     bg='#40371f' if name in temporary else '#293d49',
                     fg=WARNING if name in temporary else '#b9dfd7', font=('Segoe UI', 8, 'bold')).pack(pady=2)
            if name in preparation:
                tk.Label(card, text='UNLOCK PREPARATION\n→ ' + ', '.join(preparation[name]),
                         bg=PANEL, fg=WARNING, wraplength=104, font=('Segoe UI', 8)).pack(pady=2)
            if champion['unlockable']:
                tk.Button(card, text='Conditional · details', command=lambda name=name: self.on_unlock(name),
                          bg=PANEL, fg=WARNING, activebackground='#253047', activeforeground='white',
                          relief='flat', borderwidth=0, cursor='hand2', font=('Segoe UI', 8, 'underline')).pack()
            detail = champion_details(
                champion, stars, ' · '.join(status),
                next((marker.get('reason', '') for marker in board.get('positioning', [])
                      if marker['champion'] == name), ''),
                holder_kinds, support)
            for widget in (card, *card.winfo_children()):
                tip.bind(widget, detail)
            cards.append(card)
        self._reflow_grid(portraits, cards, 132)
        position_board = BoardView(section, self.champions, self.artwork, self.on_unlock)
        position_board.configure(height=280)
        position_board.pack(fill='x', pady=(10, 4))
        position_board.show_comp(board, self.collab_pool, set(), board['planned_stars'])
        active = [trait for trait in board['traits'] if trait['tier']]
        self._text(section, 'Active traits', ACCENT, bold=True, pady=(10, 3))
        if not active:
            self._text(section, 'No trait breakpoint is active on this board.', MUTED)
        else:
            traits_frame = tk.Frame(section, bg=PANEL)
            traits_frame.pack(fill='x')
            active.sort(key=lambda trait: (STYLE_ORDER[badge_style(trait, self.definitions[trait['name']])],
                                           -trait['count'], trait['name']))
            rows = [TraitRow(traits_frame, trait, self.definitions[trait['name']], self.artwork,
                             on_wheel=self._wheel,
                             members=trait_members(self.data, trait['name'], self.collab_pool, self.sona_band))
                    for trait in active]
            self._reflow_grid(traits_frame, rows, 318)
        if board.get('frontline_anchor'):
            self._text(section, 'Planned frontline anchor: ' + board['frontline_anchor'], MUTED, pady=(8, 3))
        for note in board.get('strategy_notes', []):
            self._text(section, note, MUTED)

    @staticmethod
    def _item_build_details(parent, build, wraplength=None):
        tk.Label(parent, text=build.get('label', 'Recommended items') + ' · not owned.', bg=PANEL, fg=MUTED,
                 wraplength=wraplength or 400, font=('Segoe UI', 8)).pack(anchor='w')
        if build.get('note') and wraplength is None:
            tk.Label(parent, text=build['note'], bg=PANEL, fg=MUTED,
                     wraplength=wraplength or 400, font=('Segoe UI', 8),
                     justify='left').pack(anchor='w')
        if build.get('source'):
            tk.Button(parent, text='Item build source ↗',
                      command=lambda url=build['source']: webbrowser.open(url),
                      bg=PANEL, fg=ACCENT, activebackground='#253047', activeforeground='white',
                      relief='flat', cursor='hand2', wraplength=wraplength or 400,
                      font=('Segoe UI', 8, 'underline')).pack(anchor='w')

    def _unlock_prep(self, step):
        preparation = step.get('unlock_prep', [])
        if not preparation:
            return
        section = self._section('Unlock preparation — intentional helpers for future targets')
        self._text(section, 'These helpers prepare the listed end-goal quests, not unrelated chain recommendations. '
                   'Their displayed stars are planning assumptions, not proof of the required upgrade, item or progress. '
                   'Keep the setup until the full task is earned; only then make the conditional handoff.', WARNING)
        self._text(section, 'Field-member tasks require different champions, not a trait’s aggregate star count. '
                   'After the required combat or other progress, confirm the actual unlock in game before handing off.',
                   WARNING)
        for unlock in preparation:
            task = tk.Frame(section, bg=PANEL)
            task.pack(fill='x', pady=(7, 5))
            name = unlock['name']
            tk.Button(task, text='Future target: ' + name + ' — sourced unlock details ↗',
                      command=lambda name=name: self.on_unlock(name),
                      bg=PANEL, fg=WARNING, activebackground='#253047', activeforeground='white',
                      relief='flat', borderwidth=0, cursor='hand2', anchor='w',
                      font=('Segoe UI', 10, 'bold')).pack(anchor='w')
            self._text(task, 'Preparation helpers: ' + ', '.join(
                f"{helper} ({star_text(step['stars'].get(helper, 1))} planned)"
                for helper in unlock['helpers']), MUTED)
            self._text(task, 'Required task — not yet confirmed: ' + unlock['condition'])
            if unlock.get('count_mode') == 'distinct_champions':
                traits = ' / '.join(unlock['traits'])
                self._text(task, f"Setup requirement: field {unlock['count']} different {traits} champions; "
                           'aggregate trait stars do not satisfy this task.', WARNING)
            self._text(task, 'Published player-level requirement: ' + unlock['level'], WARNING)
            if unlock.get('note'):
                self._text(task, unlock['note'], WARNING)
            self._text(task, 'Source: ' + unlock['source'], MUTED)

    def _unlocks(self, step):
        if not step['unlocks']:
            return
        section = self._section('Selected board’s required unlocks — verify every task before fielding')
        self._text(section, 'These are conditions, not confirmed rewards. Published minimums and '
                   'planning availability are distinct; “Any” does not guarantee shop access.', WARNING)
        for unlock in step['unlocks']:
            name = unlock['name']
            task = tk.Frame(section, bg=PANEL)
            task.pack(fill='x', pady=(7, 5))
            tk.Button(task, text=name + ' — sourced unlock details ↗', command=lambda name=name: self.on_unlock(name),
                      bg=PANEL, fg=WARNING, activebackground='#253047', activeforeground='white',
                      relief='flat', borderwidth=0, cursor='hand2', anchor='w', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
            self._text(task, unlock['condition'])
            self._text(task, 'Published player-level requirement: ' + unlock['level'], WARNING)
            if unlock.get('note'):
                self._text(task, unlock['note'], WARNING)
            self._text(task, 'Source: ' + unlock['source'], MUTED)

    def copy_buildup(self):
        if self.busy or self.result is None:
            return 'break'
        self.clipboard_clear()
        self.clipboard_append(tr(format_buildup(self.result, self.champions)))
        self.copy_status.configure(text='Complete plan copied')
        return 'break'
