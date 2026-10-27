"""A scrollable form that keeps natural field heights on smaller displays."""
import tkinter as tk
from tkinter import ttk


class ScrollableSettings(ttk.Frame):
    def __init__(self, parent, background):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0,
                                width=380, height=400, yscrollincrement=20)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky='ns', padx=(6,0))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0,0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', self.update_region)
        self.canvas.bind('<Configure>', self.resize_content)
        self.wheel_tag = f'StudioSettingsWheel{id(self)}'
        self.canvas.bind_class(self.wheel_tag, '<MouseWheel>', self.on_wheel)
        self.canvas.bind_class(self.wheel_tag, '<FocusIn>', self.on_focus)

    def resize_content(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self.update_region()

    def update_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def bind_form(self):
        """Route wheel events only within this form; leave gallery/preview alone."""
        def visit(widget):
            widget.bindtags((self.wheel_tag,) + tuple(tag for tag in widget.bindtags() if tag != self.wheel_tag))
            for child in widget.winfo_children(): visit(child)
        visit(self.content)
        self.canvas.bindtags((self.wheel_tag,) + self.canvas.bindtags())

    def on_wheel(self, event):
        if not event.delta: return
        # A long prompt scrolls within its own editor until its end is reached.
        if isinstance(event.widget, tk.Text):
            first, last = event.widget.yview()
            if (event.delta > 0 and first > 0) or (event.delta < 0 and last < 1):
                return
        units = max(1, abs(int(event.delta)) // 120) * 3
        self.canvas.yview_scroll(-units if event.delta > 0 else units, 'units')
        return 'break'

    def on_focus(self, event):
        widget = event.widget
        top = widget.winfo_rooty() - self.canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        height = self.canvas.winfo_height()
        offset = top if top < 0 else bottom - height if bottom > height else 0
        if offset:
            region = self.canvas.bbox('all')
            if region and region[3] > 0:
                # Leave a margin because Canvas rounds scrolling to 20px units.
                offset += 20 if offset > 0 else -20
                self.canvas.yview_moveto((self.canvas.canvasy(0) + offset) / region[3])
