"""Desktop orb and hover/pin/collapse state, independent of the market feed."""
import ctypes
import time
import tkinter as tk
from ctypes import wintypes
from native_image import LayeredImage, round_panel
from market import RESOURCE_BASE


def work_area(root):
    area = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0):
        return area.left, area.top, area.right, area.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def clamp(value, low, high):
    return max(low, min(value, max(low, high)))


class Orbit:
    SIZE = 144

    def __init__(self, app):
        self.app, self.root = app, app.root
        self.pinned = False
        self.visible = False
        self.suppressed = False
        self.dragging = False
        self.pressed = None
        self.outside_since = None
        self.disposed = False
        self.pointer = self.root.winfo_pointerxy
        self.ball = tk.Toplevel(self.root)
        self.ball.withdraw()
        self.ball.title("毛球加密货币桌面看板（阿杜） · 绿色毛线球")
        self.ball.overrideredirect(True)
        self.ball.configure(bg="#010203")
        self.ball.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.ball, width=self.SIZE, height=self.SIZE,
                                bg="#010203", highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        left, top, right, bottom = work_area(self.root)
        pos = app.feed.settings.get("orb_position", [right - 170, top + 160])
        self.x = clamp(int(pos[0]), left, right - self.SIZE)
        self.y = clamp(int(pos[1]), top, bottom - self.SIZE)
        self.ball.geometry(f"{self.SIZE}x{self.SIZE}+{self.x}+{self.y}")
        self.surface = LayeredImage(self.ball, RESOURCE_BASE / "assets" / "wool-orb.png", self.SIZE)
        self.canvas.bind("<Enter>", self.enter)
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Button-3>", self.menu)
        self.canvas.bind("<Return>", lambda e: self.toggle())
        self.canvas.bind("<space>", lambda e: self.toggle())
        self.root.bind("<Escape>", lambda e: self.collapse())
        self.root.protocol("WM_DELETE_WINDOW", self.collapse)
        self.ball.deiconify()
        self.draw()
        self.ball.lift()
        self.root.withdraw()
        self.poll()

    def draw(self):
        self.surface.draw()

    def position_panel(self):
        self.root.update_idletasks()
        left, top, right, bottom = work_area(self.root)
        width = min(max(1180, self.root.winfo_width()), right-left-24)
        height = min(max(800, self.root.winfo_height()), bottom-top-24)
        x = self.x + self.SIZE + 10
        if x + width > right - 12:
            x = self.x - width - 10
        x = clamp(x, left+12, right-width-12)
        y = clamp(self.y - 46, top+12, bottom-height-12)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        round_panel(self.root)

    def show(self, pin=False):
        if self.disposed:
            return
        if pin:
            self.pinned = True
        if not self.visible:
            self.position_panel()
            self.root.deiconify()
            self.visible = True
        self.root.lift()
        self.ball.lift()
        self.outside_since = None
        self.app.mode_label.config(text="已固定 · 再点圆球收起" if self.pinned else "悬停预览 · 点击圆球固定")
        self.draw()

    def enter(self, event=None):
        if not self.suppressed and not self.dragging:
            self.show()

    def toggle(self):
        if self.pinned:
            self.collapse()
        else:
            self.suppressed = False
            self.show(pin=True)

    def collapse(self):
        self.pinned = False
        self.visible = False
        self.suppressed = True
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel) and child != self.ball:
                child.destroy()
        self.root.withdraw()
        self.draw()

    def press(self, event):
        self.pressed = (event.x_root, event.y_root, self.x, self.y)
        self.dragging = False

    def motion(self, event):
        if self.pressed is None:
            return
        px, py, x, y = self.pressed
        dx, dy = event.x_root - px, event.y_root - py
        if not self.dragging and abs(dx) + abs(dy) < 6:
            return
        self.dragging = True
        left, top, right, bottom = work_area(self.root)
        self.x = clamp(x+dx, left, right-self.SIZE)
        self.y = clamp(y+dy, top, bottom-self.SIZE)
        self.ball.geometry(f"+{self.x}+{self.y}")
        if self.visible:
            self.position_panel()

    def release(self, event):
        if self.pressed is None:
            return
        dragged = self.dragging
        self.pressed = None
        self.dragging = False
        if dragged:
            self.save_position()
        else:
            self.toggle()

    def save_position(self):
        self.app.feed.settings["orb_position"] = [self.x, self.y]
        self.app.save_settings()

    def over(self, window, x, y):
        if not window.winfo_viewable():
            return False
        if window == self.ball:
            return self.surface.hit(x-window.winfo_rootx(), y-window.winfo_rooty())
        return (window.winfo_rootx() <= x < window.winfo_rootx()+window.winfo_width()
                and window.winfo_rooty() <= y < window.winfo_rooty()+window.winfo_height())

    def poll(self):
        if self.disposed or not self.app.alive:
            return
        x, y = self.pointer()
        over_ball = self.over(self.ball, x, y)
        if over_ball and not self.visible and not self.suppressed and not self.dragging:
            self.show()
        if not over_ball:
            self.suppressed = False
        if self.visible and not self.pinned and not self.dragging:
            # An open settings/watchlist dialog keeps its parent visible.
            dialogs = [w for w in self.root.winfo_children()
                       if isinstance(w, tk.Toplevel) and w != self.ball and w.winfo_viewable()]
            inside = over_ball or self.over(self.root, x, y) or bool(dialogs)
            if inside:
                self.outside_since = None
            elif self.outside_since is None:
                self.outside_since = time.monotonic()
            elif time.monotonic() - self.outside_since > 0.45:
                self.visible = False
                self.root.withdraw()
                self.outside_since = None
        self.root.after(80, self.poll)

    def menu(self, event):
        menu = tk.Menu(self.ball, tearoff=False, bg="#232932", fg="#e1e5ea",
                       activebackground="#35434b", activeforeground="#ffffff")
        menu.add_command(label="固定展开", command=lambda: self.show(pin=True))
        menu.add_command(label="收起面板", command=self.collapse)
        menu.add_separator()
        menu.add_command(label="退出行情窗", command=self.app.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def dispose(self):
        self.disposed = True
        self.ball.destroy()
