"""The four requested usage steps; no game automation or extra onboarding topics."""
import tkinter as tk
from tkinter import ttk

from localization import tr, display_text, localize_tree
from tutorial_content import TUTORIAL_STEPS




class TutorialWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('How to use')
        self.geometry('700x470')
        self.minsize(540, 360)
        self.transient(parent)
        self.index = 0
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.heading = ttk.Label(self, font=('Segoe UI', 14, 'bold'), padding=(18, 14))
        self.heading.grid(row=0, column=0, sticky='ew')
        self.text = tk.Text(self, wrap='word', bg='#192337', fg='#e5e7eb',
                            font=('Segoe UI', 11), relief='flat', padx=18, pady=14)
        self.text._preserve_text = True
        self.text.grid(row=1, column=0, sticky='nsew', padx=12)
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure('key', foreground='#c4b5fd', font=('Segoe UI', 11, 'bold'), spacing1=10)
        controls = ttk.Frame(self, padding=14)
        controls.grid(row=2, column=0, columnspan=2, sticky='ew')
        self.language_button = ttk.Button(controls, text='Open language settings', command=parent.language_settings)
        self.language_button.pack(side='left')
        self.previous = ttk.Button(controls, text='Previous', command=lambda: self.select_step(self.index - 1))
        self.previous.pack(side='right', padx=4)
        self.next = ttk.Button(controls, text='Next', command=lambda: self.select_step(self.index + 1))
        self.next.pack(side='right', padx=4)
        ttk.Button(controls, text='Close', command=self.destroy).pack(side='right', padx=4)
        self.refresh()

    def select_step(self, index):
        if not 0 <= index < len(TUTORIAL_STEPS):
            raise ValueError('Tutorial step is outside the four-step guide.')
        self.index = index
        self.refresh()

    def refresh(self):
        title, sections = TUTORIAL_STEPS[self.index]
        self.heading.configure(text=f'Step {self.index + 1} of 4 — {title}')
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for key, wording in sections:
            self.text.insert('end', display_text(tr(key)) + '\n', 'key')
            self.text.insert('end', display_text(tr(wording)) + '\n\n')
        self.text.configure(state='disabled')
        self.previous.state(['disabled'] if self.index == 0 else ['!disabled'])
        self.next.state(['disabled'] if self.index == len(TUTORIAL_STEPS) - 1 else ['!disabled'])
        if self.index == 0:
            self.language_button.pack(side='left')
        else:
            self.language_button.pack_forget()
        localize_tree(self)
