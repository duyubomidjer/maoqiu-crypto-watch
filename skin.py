"""Champagne UI chrome from the approved Image Gen asset, with live Tk text."""
from functools import lru_cache
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageEnhance, ImageDraw, ImageFont
from market import RESOURCE_BASE

BG = "#1c2127"
PANEL = "#20262c"
TEXT = "#f0eee8"
MUTED = "#adb2ba"
GOLD = "#e6c69c"


@lru_cache(maxsize=1)
def atlas():
    with Image.open(RESOURCE_BASE / "assets" / "champagne-skin.png") as source:
        return source.convert("RGB")


@lru_cache(maxsize=2)
def alert_tile(positive):
    with Image.open(RESOURCE_BASE / "assets" / "champagne-alerts.png") as source:
        w, h = source.size
        tile = source.convert("RGBA").crop((w//2 if positive else 0, 0, w if positive else w//2, h))
        bounds = tile.getchannel("A").point(lambda alpha: 255 if alpha > 32 else 0).getbbox()
        return tile.crop(bounds)


def nine_slice(source, size, edge=12, center=True):
    """Scale only the middle of each edge; keep the original rounded corners."""
    w, h = size
    sw, sh = source.size
    edge = min(edge, w // 2, h // 2, sw // 2, sh // 2)
    out = Image.new("RGB", size, BG)
    xs, ys = (0, edge, sw-edge, sw), (0, edge, sh-edge, sh)
    dx, dy = (0, edge, w-edge, w), (0, edge, h-edge, h)
    for r in range(3):
        for c in range(3):
            if not center and r == c == 1:
                continue
            tile = source.crop((xs[c], ys[r], xs[c+1], ys[r+1]))
            tile = tile.resize((dx[c+1]-dx[c], dy[r+1]-dy[r]), Image.Resampling.LANCZOS)
            out.paste(tile, (dx[c], dy[r]))
    return out


class PanelRim(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=BG, bd=0, highlightthickness=0)
        self.source = atlas().crop((42, 83, 1320, 928))
        self.item = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", self.render)

    def render(self, event):
        if min(event.width, event.height) < 40:
            return
        self.photo = ImageTk.PhotoImage(nine_slice(self.source, (event.width, event.height), 22, False))
        self.itemconfigure(self.item, image=self.photo)


class MetalButton(tk.Button):
    """Native command/state/keyboard semantics over a resizable raster button."""
    def __init__(self, parent, text, command, accent=False):
        font = ("Microsoft YaHei UI", 13, "bold" if accent else "normal")
        width = max(94, tkfont.Font(font=font).measure(text)+38)
        if text == "立即刷新":
            width = 178  # Includes the longest countdown label without reflow.
        source = atlas().crop((75, 202, 280, 263) if accent else (1015, 117, 1131, 176))
        bitmap = nine_slice(source, (width, 50), 12)
        # Use Windows' installed MDL2 icon library, not hand-drawn glyphs.
        icons = {"立即刷新": "\ue72c", "+ 自选": "\ue710", "设置": "\ue713", "收起": "\ue738"}
        self.icon = icons.get(text)
        if self.icon:
            icon_font = ImageFont.truetype("C:/Windows/Fonts/segmdl2.ttf", 21)
            ImageDraw.Draw(bitmap).text((14, 25), self.icon, font=icon_font,
                fill="#211c16" if accent else TEXT, anchor="lm")
            text = "   " + text.lstrip("+ ")
        self.normal_image = ImageTk.PhotoImage(bitmap)
        self.hover_image = ImageTk.PhotoImage(ImageEnhance.Brightness(bitmap).enhance(1.10))
        super().__init__(parent, text=text, command=command, image=self.normal_image,
                         compound="center", bg=BG, fg="#211c16" if accent else TEXT,
                         activebackground=BG, activeforeground="#211c16" if accent else TEXT,
                         disabledforeground="#746b61", font=font, bd=0, relief="flat",
                         highlightthickness=0, padx=0, pady=0, cursor="hand2", takefocus=True)
        self.bind("<Enter>", lambda e: self.configure(image=self.hover_image))
        self.bind("<Leave>", lambda e: self.configure(image=self.normal_image))
        self.bind("<FocusIn>", lambda e: self.configure(image=self.hover_image))
        self.bind("<FocusOut>", lambda e: self.configure(image=self.normal_image))

    def config(self, cnf=None, **kwargs):
        if "text" in kwargs and self.icon:
            kwargs["text"] = "   " + kwargs["text"].lstrip("+ ")
        super().config(cnf, **kwargs)

    configure = config


class HeaderLabel(tk.Canvas):
    def __init__(self, parent, text, column, span, first=False, last=False):
        super().__init__(parent, bg=BG, height=56, width=span, bd=0, highlightthickness=0)
        self.font = ("Microsoft YaHei UI", 13, "bold")
        self.column = column
        self.first, self.last = first, last
        self.text = text
        self.back = self.create_image(0, 0, anchor="nw")
        self.caption = self.create_text(0, 0, text=text, fill=TEXT, font=self.font, anchor="w" if first else "e")
        self.bind("<Configure>", self.render)

    def cget(self, key):
        return self.font if key == "font" else super().cget(key)

    def render(self, event):
        # Each column samples its segment of the same continuous machined band.
        total = self.master.winfo_width()
        band = nine_slice(atlas().crop((69, 273, 1294, 339)), (max(40, total), event.height), 12)
        x = self.winfo_x()
        self.photo = ImageTk.PhotoImage(band.crop((x, 0, x+event.width, event.height)))
        self.itemconfigure(self.back, image=self.photo)
        self.coords(self.caption, 22 if self.first else event.width-14, event.height/2)


class TextSlot:
    """Small adapter preserving the feed renderer's existing label interface."""
    def __init__(self, master, detail=False):
        self.master, self.detail = master, detail
        self.values = {"text": "", "fg": MUTED if detail else TEXT, "bg": PANEL}

    def config(self, **kwargs):
        changed = any(self.values.get(k) != v for k, v in kwargs.items())
        self.values.update(kwargs)
        if changed:
            self.master.render_text()

    configure = config

    def cget(self, key):
        return self.values.get(key)

    def winfo_rooty(self):
        return self.master.winfo_rooty()


class MarketCell(tk.Canvas):
    def __init__(self, parent, column, icon=None):
        super().__init__(parent, bg=PANEL, height=80, width=1, bd=0, highlightthickness=0)
        self.column, self.icon = column, icon
        self.value, self.detail = TextSlot(self), TextSlot(self, True)
        self.back = self.create_image(0, 0, anchor="nw")
        self.alert = self.create_image(0, 0, anchor="center", state="hidden")
        self.alert_photos = {}
        self.active_alert_photo = None  # Keep the canvas image alive across cache resets.
        self.icon_item = self.create_image(20, 37, image=icon, anchor="w") if icon else None
        self.value_item = self.create_text(0, 0, anchor="w" if column == 0 else "e",
            font=("Segoe UI" if column < 4 or column == 5 else "Microsoft YaHei UI", 17,
                  "bold" if column == 0 else "normal"), fill=TEXT)
        self.detail_item = self.create_text(0, 0, anchor="w" if column == 0 else "e",
            font=("Segoe UI", 10), fill=MUTED)
        self.bind("<Configure>", self.render)

    def config(self, cnf=None, **kwargs):
        before = self.cget("bg")
        super().config(cnf, **kwargs)
        if "bg" in kwargs and before != kwargs["bg"]:
            self.render_alert()

    configure = config

    def render(self, event):
        total = max(40, self.master.winfo_width())
        row = nine_slice(atlas().crop((69, 339, 1294, 422)), (total, event.height), 8)
        x = self.winfo_x()
        self.photo = ImageTk.PhotoImage(row.crop((x, 0, x+event.width, event.height)))
        self.itemconfigure(self.back, image=self.photo)
        if self.icon_item:
            self.coords(self.icon_item, 20, event.height/2)
        self.alert_photos.clear()
        self.render_alert()
        self.render_text()

    def render_alert(self):
        active = self.cget("bg") != PANEL
        if not active or self.winfo_width() <= 12 or self.winfo_height() <= 20:
            # Detach from Tk before releasing the last Python reference.
            self.itemconfigure(self.alert, image="", state="hidden")
            self.active_alert_photo = None
            return
        positive = self.cget("bg") == "#30493f"
        if positive not in self.alert_photos:
            tile = alert_tile(positive).resize((self.winfo_width()-12, self.winfo_height()-20), Image.Resampling.LANCZOS)
            self.alert_photos[positive] = ImageTk.PhotoImage(tile, master=self)
        photo = self.alert_photos[positive]
        # Replace image and state together while the previous image is still alive.
        self.itemconfigure(self.alert, image=photo, state="normal")
        self.active_alert_photo = photo
        self.coords(self.alert, self.winfo_width()/2, self.winfo_height()/2)

    def render_text(self):
        w, h = self.winfo_width(), self.winfo_height()
        x = (76 if self.icon else 22) if self.column == 0 else w-14
        has_detail = bool(self.detail.values["text"])
        self.coords(self.value_item, x, h/2-10 if has_detail else h/2)
        self.coords(self.detail_item, x, h/2+15)
        self.itemconfigure(self.value_item, text=self.value.values["text"], fill=self.value.values["fg"])
        self.itemconfigure(self.detail_item, text=self.detail.values["text"], fill=self.detail.values["fg"])
