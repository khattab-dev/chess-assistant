"""Inline controls with live preview; no settings dialogs."""
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from .arrows import ArrowStyle, PRESETS, render_preview


class ArrowControls(ttk.LabelFrame):
    def __init__(self, master, style: ArrowStyle, changed, warn):
        super().__init__(master, text="Arrow style")
        self.changed, self.warn = changed, warn
        self.style = style
        self.preset, self.hex_color = tk.StringVar(self), tk.StringVar(self)
        self.opacity, self.thickness = tk.DoubleVar(self), tk.DoubleVar(self)
        self.opacity_text, self.thickness_text = tk.StringVar(self), tk.StringVar(self)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="Color").pack(side="left")
        self.color_choice = ttk.Combobox(row, values=(*PRESETS, "Custom"),
                                         textvariable=self.preset, state="readonly", width=8)
        self.color_choice.pack(side="left", padx=5)
        self.color_choice.bind("<<ComboboxSelected>>", lambda _: self._preset_changed())
        self.hex_entry = ttk.Entry(row, textvariable=self.hex_color, width=9)
        self.hex_entry.pack(side="left", padx=(0, 8))
        self.hex_entry.bind("<Return>", lambda _: self._hex_changed())
        self.hex_entry.bind("<FocusOut>", lambda _: self._hex_changed())
        self.swatch = tk.Label(row, width=2, relief="solid", borderwidth=1)
        self.swatch.pack(side="left")
        ttk.Label(row, text="Opacity").pack(side="left", padx=(14, 4))
        self.opacity_scale = ttk.Scale(row, from_=0, to=100, variable=self.opacity,
                                       length=115, command=lambda _: self._sliders_changed())
        self.opacity_scale.pack(side="left")
        ttk.Label(row, textvariable=self.opacity_text, width=5).pack(side="left", padx=4)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=6, pady=(0, 5))
        ttk.Label(row, text="Thickness").pack(side="left")
        self.thickness_scale = ttk.Scale(row, from_=50, to=200, variable=self.thickness,
                                         length=115, command=lambda _: self._sliders_changed())
        self.thickness_scale.pack(side="left", padx=5)
        ttk.Label(row, textvariable=self.thickness_text, width=5).pack(side="left")
        self.preview = tk.Canvas(row, width=120, height=40, highlightthickness=0)
        self.preview.pack(side="left", padx=(12, 18))
        for index in range(3):
            self.preview.create_rectangle(index * 40, 0, (index + 1) * 40, 40,
                                           fill=("#eeeed2" if index % 2 == 0 else "#769656"), outline="")
        ttk.Button(row, text="Reset defaults", command=self._reset).pack(side="left")
        self.set_style(style)

    def set_style(self, style):
        self.style = style
        self.preset.set(next((name for name, color in PRESETS.items() if color == style.color), "Custom"))
        self.hex_color.set(style.color)
        self.opacity.set(style.opacity)
        self.thickness.set(style.thickness)
        self.opacity_text.set(f"{style.opacity}%")
        self.thickness_text.set(f"{style.thickness}%")
        self.swatch.config(bg=style.color)
        self.preview.delete("arrow")
        self._preview_image = ImageTk.PhotoImage(render_preview(style), master=self)
        self.preview.create_image(0, 0, anchor="nw", image=self._preview_image, tags="arrow")

    def _apply(self, style):
        previous = self.style
        self.set_style(style)
        if style != previous:
            self.changed(style)

    def _preset_changed(self):
        color = PRESETS.get(self.preset.get())
        if color:
            self._apply(ArrowStyle(color, self.style.opacity, self.style.thickness))
        else:
            self.hex_entry.focus_set()

    def _hex_changed(self):
        try:
            self._apply(ArrowStyle(self.hex_color.get().strip(), self.style.opacity, self.style.thickness))
        except ValueError as error:
            self.hex_color.set(self.style.color)
            self.warn(str(error), "orange")

    def _sliders_changed(self):
        self._apply(ArrowStyle(self.style.color, round(self.opacity.get()), round(self.thickness.get())))

    def _reset(self):
        self._apply(ArrowStyle())
