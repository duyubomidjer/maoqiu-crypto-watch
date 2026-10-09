"""Render a PNG in a Windows layered window, preserving per-pixel alpha."""
import ctypes as C
from ctypes import wintypes as W
from PIL import Image, ImageChops


class BitmapHeader(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                ("image_size", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                ("used", W.DWORD), ("important", W.DWORD)]


class BitmapInfo(C.Structure):
    _fields_ = [("header", BitmapHeader), ("colors", W.DWORD * 3)]


class Blend(C.Structure):
    _fields_ = [("op", W.BYTE), ("flags", W.BYTE), ("alpha", W.BYTE), ("format", W.BYTE)]


class LayeredImage:
    def __init__(self, window, path, size):
        self.window = window
        # Runtime scaling only; the original selected PNG remains untouched.
        with Image.open(path) as source:
            self.image = source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        self.alpha = self.image.getchannel("A")
        r, g, b, a = self.image.split()
        premultiplied = Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                             ImageChops.multiply(b, a), a))
        self.pixels = premultiplied.tobytes("raw", "BGRA")
        self.size = size
        self.user = C.WinDLL("user32", use_last_error=True)
        self.gdi = C.WinDLL("gdi32", use_last_error=True)
        self.user.GetAncestor.argtypes = [W.HWND, W.UINT]
        self.user.GetAncestor.restype = W.HWND
        self.user.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
        self.user.GetWindowLongPtrW.restype = C.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = C.c_ssize_t
        self.user.GetDC.argtypes = [W.HWND]
        self.user.GetDC.restype = W.HDC
        self.user.ReleaseDC.argtypes = [W.HWND, W.HDC]
        self.user.UpdateLayeredWindow.argtypes = [W.HWND, W.HDC, C.POINTER(W.POINT),
            C.POINTER(W.SIZE), W.HDC, C.POINTER(W.POINT), W.DWORD, C.POINTER(Blend), W.DWORD]
        self.user.UpdateLayeredWindow.restype = W.BOOL
        self.gdi.CreateCompatibleDC.argtypes = [W.HDC]
        self.gdi.CreateCompatibleDC.restype = W.HDC
        self.gdi.CreateDIBSection.argtypes = [W.HDC, C.POINTER(BitmapInfo), W.UINT,
                                            C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
        self.gdi.CreateDIBSection.restype = W.HBITMAP
        self.gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]
        self.gdi.SelectObject.restype = W.HANDLE
        self.gdi.DeleteObject.argtypes = [W.HANDLE]
        self.gdi.DeleteDC.argtypes = [W.HDC]
        window.update_idletasks()
        self.hwnd = self.user.GetAncestor(window.winfo_id(), 2)
        style = self.user.GetWindowLongPtrW(self.hwnd, -20)
        # Clear prior color-key/global-opacity state before using UpdateLayeredWindow.
        self.user.SetWindowLongPtrW(self.hwnd, -20, style & ~0x80000)
        self.user.SetWindowLongPtrW(self.hwnd, -20, style | 0x80000 | 0x80)

    def hit(self, x, y):
        return 0 <= x < self.size and 0 <= y < self.size and self.alpha.getpixel((int(x), int(y))) > 16

    def draw(self):
        self.window.update_idletasks()
        screen = self.user.GetDC(None)
        memory = self.gdi.CreateCompatibleDC(screen)
        bitmap = old = None
        try:
            info = BitmapInfo()
            info.header = BitmapHeader(C.sizeof(BitmapHeader), self.size, -self.size, 1, 32, 0,
                                       self.size * self.size * 4, 0, 0, 0, 0)
            bits = C.c_void_p()
            bitmap = self.gdi.CreateDIBSection(screen, C.byref(info), 0, C.byref(bits), None, 0)
            if not bitmap or not memory:
                raise C.WinError(C.get_last_error())
            C.memmove(bits, self.pixels, len(self.pixels))
            old = self.gdi.SelectObject(memory, bitmap)
            point = W.POINT(self.window.winfo_rootx(), self.window.winfo_rooty())
            dimensions = W.SIZE(self.size, self.size)
            origin = W.POINT(0, 0)
            blend = Blend(0, 0, 255, 1)
            if not self.user.UpdateLayeredWindow(self.hwnd, screen, C.byref(point), C.byref(dimensions),
                    memory, C.byref(origin), 0, C.byref(blend), 2):
                raise C.WinError(C.get_last_error())
        finally:
            if old:
                self.gdi.SelectObject(memory, old)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)
            self.user.ReleaseDC(None, screen)


def round_panel(window):
    """Native window region: normal controls remain live, no screenshot-as-UI."""
    dimensions = (window.winfo_width(), window.winfo_height())
    if getattr(window, "_rounded_size", None) == dimensions or min(dimensions) < 2:
        return
    window._rounded_size = dimensions
    user, gdi = C.windll.user32, C.windll.gdi32
    user.GetAncestor.argtypes = [W.HWND, W.UINT]
    user.GetAncestor.restype = W.HWND
    gdi.CreateRoundRectRgn.argtypes = [C.c_int] * 6
    gdi.CreateRoundRectRgn.restype = W.HANDLE
    user.SetWindowRgn.argtypes = [W.HWND, W.HANDLE, W.BOOL]
    gdi.DeleteObject.argtypes = [W.HANDLE]
    region = gdi.CreateRoundRectRgn(0, 0, window.winfo_width()+1, window.winfo_height()+1, 28, 28)
    if not user.SetWindowRgn(user.GetAncestor(window.winfo_id(), 2), region, True):
        gdi.DeleteObject(region)
