"""A small Windows desktop watchlist; run with pythonw app.py."""
import argparse
import ctypes
import json
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw, ImageTk

from market import BASE, RESOURCE_BASE, Feed, INTERVALS, age_is_stale, error_label, number
from skin import PanelRim, MetalButton, HeaderLabel, MarketCell
from diagnostics import Diagnostics

BG, PANEL, LINE = "#1c2127", "#20262c", "#3e444b"
TEXT, MUTED, GREEN, RED, AMBER = "#f0eee8", "#adb2ba", "#4de3b6", "#ff6078", "#e6c69c"
ACCENT = "#e6c69c"
APP_TITLE = "毛球加密货币桌面看板（阿杜）"


def money(value, compact=False):
    n = number(value)
    if n is None:
        return "—"
    if compact:
        for unit, factor in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4)):
            if abs(n) >= factor:
                return f"{n / factor:,.2f}{unit}"
    if abs(n) >= 100:
        return f"{n:,.2f}"
    if abs(n) >= 1:
        return f"{n:,.4f}"
    if 0 < abs(n) < 1e-4:
        return f"{n:.6g}"  # Scientific notation keeps tiny nonzero prices visible.
    return f"{n:.8f}".rstrip("0").rstrip(".") if n else "0"


def percent(n):
    n = number(n)
    return "—" if n is None else f"{n:+.2f}%"


def flash_background(value, stale, now):
    n = number(value)
    if n is None or stale or abs(n) <= 10 or int(now) % 2:
        return PANEL
    return "#30493f" if n > 0 else "#543640"


def label(parent, text="", size=10, color=TEXT, bold=False, family="Microsoft YaHei UI", **kw):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(family, size, "bold" if bold else "normal"), **kw)


def button(parent, text, command, accent=False):
    return MetalButton(parent, text, command, accent)


class WatchWindow:
    def __init__(self, root, feed, orb_mode=True):
        self.root, self.feed = root, feed
        self.orbit = None
        if orb_mode:
            root.withdraw()
            root.overrideredirect(True)
        self.events = queue.Queue()
        self.rows = {}
        self.asset_icons = {}
        self.manager = None
        self.alive = True
        self._tick_job = None
        self.ui_error_until = 0
        self.notice_ack = False
        self.diagnostics = Diagnostics(feed.directory)
        root.report_callback_exception = lambda kind, value, tb: self.report_ui_error("tk-callback", value)
        root.title(APP_TITLE)
        root.configure(bg=BG, highlightthickness=0)
        self.rim = PanelRim(root)
        self.rim.place(x=0, y=0, relwidth=1, relheight=1)
        self.content = tk.Frame(root, bg=BG)
        self.content.pack(fill="both", expand=True, padx=12, pady=12)
        root.minsize(1080, 460)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                        arrowcolor=MUTED, lightcolor=LINE, darkcolor=LINE)
        style.configure("TCombobox", fieldbackground=PANEL, background=LINE, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=LINE, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
        root.option_add("*TCombobox*Listbox.background", PANEL)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        geometry = feed.settings.get("geometry", "1180x760+80+80")
        try:
            root.geometry(geometry)
        except tk.TclError:
            root.geometry("1180x760+80+80")
        self.topmost = tk.BooleanVar(value=bool(feed.settings.get("topmost", True)))
        self.opacity = tk.DoubleVar(value=max(55, min(100, number(feed.settings.get("opacity")) or 100)))
        root.attributes("-topmost", self.topmost.get())
        root.attributes("-alpha", self.opacity.get() / 100)
        head = tk.Frame(self.content, bg=BG, padx=16, pady=16)
        head.pack(fill="x")
        tk.Frame(head, bg=ACCENT, width=3, height=33).pack(side="left", padx=(0, 14))
        label(head, APP_TITLE, size=30, bold=True, color="#f3e6ce").pack(side="left")
        label(head, "  M A R K E T  W A T C H", color=MUTED, size=10, family="Segoe UI").pack(side="left", padx=12)
        button(head, "收起", self.collapse_panel).pack(side="right")
        button(head, "设置", self.open_settings).pack(side="right", padx=8)
        button(head, "+ 自选", self.open_manager, True).pack(side="right")
        tk.Frame(self.content, bg=LINE, height=1).pack(fill="x", padx=16)
        controls = tk.Frame(self.content, bg=BG, padx=16, pady=10)
        controls.pack(fill="x")
        self.refresh_button = button(controls, "立即刷新", self.manual_refresh, True)
        self.refresh_button.pack(side="left")
        self.countdown = label(controls, color=TEXT, size=12)
        self.countdown.pack(side="right", padx=8)
        # Bottom status remains outside the scroll region, just like the header.
        foot = tk.Frame(self.content, bg=BG, padx=16, pady=9)
        foot.pack(fill="x", side="bottom")
        foot.columnconfigure(0, weight=1)
        foot.columnconfigure(1, weight=1)
        source = label(foot, "OKX 实时价格 · USDT    /    CoinGecko 综合统计 · USD", color=MUTED, size=9, cursor="hand2")
        source.grid(row=0, column=0, sticky="w", pady=(0, 5))
        source.bind("<Button-1>", lambda e: webbrowser.open("https://www.coingecko.com/en/methodology"))
        self.mode_label = label(foot, "自选市场概览", color=MUTED, size=9)
        self.mode_label.grid(row=0, column=1, sticky="e", pady=(0, 5))
        self.status = label(foot, color=GREEN, size=8, anchor="w")
        self.status.grid(row=1, column=0, sticky="w")
        self.status.bind("<Button-1>", self.show_health_notice)
        self.market_status = label(foot, color=MUTED, size=8, anchor="e")
        self.market_status.grid(row=1, column=1, sticky="e")
        self.count = label(foot, color=MUTED, size=9)
        wrapper = tk.Frame(self.content, bg=BG)
        wrapper.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        self.header = tk.Frame(wrapper, bg=BG)
        self.header.pack(fill="x")
        body = tk.Frame(wrapper, bg=BG)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.scrollbar = scroll
        self.header.pack_configure(padx=(0, scroll.winfo_reqwidth()))
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scroll_status)
        self.table = tk.Frame(self.canvas, bg=BG)
        self.table_id = self.canvas.create_window((0, 0), window=self.table, anchor="nw")
        self.table.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.table_id, width=e.width))
        root.bind("<MouseWheel>", self.scroll)
        self.rebuild()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-m>", lambda e: self.open_manager())
        grip = ttk.Sizegrip(root)
        grip.place(relx=1, rely=1, x=-9, y=-9, anchor="se")
        if orb_mode:
            from orbit import Orbit
            self.orbit = Orbit(self)
            from native_image import round_panel
            root.bind("<Configure>", lambda event: root.after_idle(lambda: round_panel(root))
                      if event.widget == root and self.alive else None, add="+")
        self.tick()

    def collapse_panel(self):
        if self.orbit:
            self.orbit.collapse()
        else:
            self.root.iconify()

    def scroll(self, event):
        if event.widget.winfo_toplevel() == self.root:
            if self.table.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def scroll_status(self, first, last):
        self.scrollbar.set(first, last)
        needed = float(last)-float(first) < .999
        if needed and not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)
            self.header.pack_configure(padx=(0, self.scrollbar.winfo_reqwidth()))
        elif not needed and self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()
            self.header.pack_configure(padx=0)

    def rebuild(self):
        for child in self.table.winfo_children():
            child.destroy()
        for child in self.header.winfo_children():
            child.destroy()
        self.rows = {}
        coins = self.feed.snapshot()["coins"]
        price_heading = "价格 USDT" if all(c["pair"] for c in coins) else "最新价格"
        headings = ["币种", price_heading, "24h", "7d", "市值 USD", "排名", "24h全市场成交额 USD"]
        widths = [190, 148, 100, 100, 142, 72, 210]
        for col, (text, width) in enumerate(zip(headings, widths)):
            self.table.columnconfigure(col, weight=1 if col != 5 else 0, minsize=width)
            self.header.columnconfigure(col, weight=1 if col != 5 else 0, minsize=width)
            header = HeaderLabel(self.header, text, col, width, first=col == 0, last=col == 6)
            header.grid(row=0, column=col, sticky="ew")
        for row, coin in enumerate(coins):
            cells = []
            for col in range(7):
                icon_image = None
                if col == 0:
                    icon_path = RESOURCE_BASE / "assets" / (coin["id"] + ".png")
                    if icon_path.exists():
                        if coin["id"] not in self.asset_icons:
                            with Image.open(icon_path) as icon:
                                avatar = icon.convert("RGBA").resize((42, 42), Image.Resampling.LANCZOS)
                                mask = Image.new("L", (42, 42), 0)
                                ImageDraw.Draw(mask).ellipse((0, 0, 41, 41), fill=255)
                                from PIL import ImageChops
                                mask = ImageChops.multiply(mask, avatar.getchannel("A"))
                                avatar.putalpha(mask)
                                if coin["id"] == "ondo-finance":
                                    backing = Image.new("RGBA", (42, 42), "#f0eee8")
                                    backing.alpha_composite(avatar)
                                    circle = Image.new("L", (42, 42), 0)
                                    ImageDraw.Draw(circle).ellipse((0, 0, 41, 41), fill=255)
                                    backing.putalpha(circle)
                                    avatar = backing
                                self.asset_icons[coin["id"]] = ImageTk.PhotoImage(avatar)
                        icon_image = self.asset_icons[coin["id"]]
                cell = MarketCell(self.table, col, icon_image)
                cell.grid(row=row, column=col, sticky="nsew")
                cells.append((cell.value, cell.detail))
            self.rows[coin["id"]] = cells
        self.count.config(text=f"{len(self.rows)} 个关注")

    def tick(self):
        if not self.alive:
            return
        if self._tick_job is not None:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        try:
            self.update_view()
        except Exception as exc:
            self.report_ui_error("refresh", exc)
            for cells in self.rows.values():
                self.mark_row_error(cells)
        finally:
            if self.alive:
                self._tick_job = self.root.after(500, self.tick)

    def report_ui_error(self, area, error):
        self.ui_error_until = time.monotonic()+15
        self.diagnostics.record(area, error)
        if self.alive and hasattr(self, "status"):
            try:
                self.status.config(text="显示异常已隔离，正在自动重试 · 点击查看", fg=AMBER)
            except tk.TclError:
                pass

    def show_health_notice(self, event=None):
        notice = self.feed.startup_notice
        if time.monotonic() < self.ui_error_until:
            notice += "\n界面遇到异常，已隔离并继续重试。诊断记录仅含时间、位置与异常类型，保存在 state/runtime-errors.log。"
        if notice:
            messagebox.showinfo("行情窗状态", notice.strip(), parent=self.root)
            self.notice_ack = True

    def mark_row_error(self, cells):
        for value, detail in cells[1:]:
            try:
                value.master.config(bg=PANEL)
                value.config(text="—", fg=AMBER, bg=PANEL)
                detail.config(text="显示异常", fg=AMBER, bg=PANEL)
            except Exception:
                pass

    def update_view(self):
        while True:
            try:
                callback = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as exc:
                self.report_ui_error("async-callback", exc)
        snap, now = self.feed.snapshot(), time.time()
        stale_prices = 0
        stale_market = 0
        for coin in snap["coins"]:
            cells = self.rows.get(coin["id"])
            if not cells:
                continue
            try:
                price_stale, stale = self.render_coin(coin, cells, snap, now)
                stale_prices += int(price_stale)
                stale_market += int(stale)
            except Exception as exc:
                stale_prices += 1
                stale_market += 1
                self.mark_row_error(cells)
                self.report_ui_error("coin-render", exc)
        suffix = f" · {stale_prices} 项价格待更新/过期" if stale_prices else " · 行情正常"
        self.status.config(text=snap["price_status"] + suffix, fg=AMBER if stale_prices else GREEN)
        if time.monotonic() < self.ui_error_until:
            self.status.config(text="显示异常已隔离，正在自动重试 · 点击查看", fg=AMBER)
        elif snap.get("startup_notice") and not self.notice_ack:
            self.status.config(text="配置或缓存已自动恢复 · 点击查看详情", fg=AMBER)
        updated = time.strftime("%H:%M:%S", time.localtime(snap["received"])) if snap["received"] else "尚未获取"
        suffix = f" · {stale_market} 项统计待更新/过期" if stale_market else ""
        self.market_status.config(text=f"{snap['market_status']} · 最近获取 {updated}" + suffix)
        cooldown = max(0, int(snap["manual_after"] - now + 0.99))
        if snap["market_busy"]:
            self.refresh_button.config(text="刷新中…", state="disabled")
            self.countdown.config(text="正在请求最新可用数据")
        else:
            self.refresh_button.config(text=f"刷新冷却 {cooldown}s" if cooldown else "立即刷新",
                                       state="disabled" if cooldown else "normal")
            remain = max(0, int(snap["next_market_at"] - now + 0.99))
            self.countdown.config(text=f"综合数据下次刷新 {remain // 60:02d}:{remain % 60:02d}  ·  自动 {snap['interval'] // 60} 分钟")

    def render_coin(self, coin, cells, snap, now):
        stats = snap["market"].get(coin["id"], {})
        stale = age_is_stale(stats.get("ts"), now, max(900, snap["interval"] + 300))
        quote = snap["prices"].get(coin["pair"], {}) if coin["pair"] else stats
        price_stale = age_is_stale(quote.get("ts"), now, 20 if coin["pair"] else max(900, snap["interval"] + 300))
        cells[0][0].config(text=coin["symbol"])
        cells[0][1].config(text="OKX · " + coin["pair"] if coin["pair"] else "综合价格 · USD")
        cells[1][0].config(text=money(quote.get("price")), fg=AMBER if price_stale else TEXT)
        cells[1][1].config(text="等待数据" if not quote else ("已过期" if price_stale else "") +
            time.strftime(" %H:%M:%S", time.localtime(quote.get("ts", 0))), fg=AMBER if price_stale else MUTED)
        for col, field in ((2, "d1"), (3, "d7")):
            n = number(stats.get(field))
            cells[col][0].config(text=percent(n), fg=MUTED if n is None else AMBER if stale else GREEN if n >= 0 else RED)
            cells[col][1].config(text="过期" if stale and stats else "", fg=MUTED)
            background = flash_background(n, stale, now)
            cells[col][0].master.config(bg=background)
            cells[col][0].config(bg=background)
            cells[col][1].config(bg=background)
        rank = number(stats.get("rank"))
        for col, text in ((4, money(stats.get("cap"), True)), (5, "—" if rank is None else f"#{int(rank)}"),
                          (6, money(stats.get("volume"), True))):
            cells[col][0].config(text=text, fg=AMBER if stale and stats else TEXT)
            cells[col][1].config(text="过期" if stale and stats else "", fg=MUTED)
        return price_stale, stale

    def manual_refresh(self):
        self.feed.request_refresh()

    def background(self, work, done, failed):
        def run():
            try:
                value = work()
                self.events.put(lambda value=value: done(value))
            except Exception as exc:
                text = error_label(exc)
                self.events.put(lambda text=text: failed(text))
        threading.Thread(target=run, daemon=True).start()

    def open_manager(self):
        if self.manager and self.manager.winfo_exists():
            self.manager.lift()
            return
        win = self.manager = tk.Toplevel(self.root)
        win.title("管理自选 · 添加 / 删除 / 排序")
        win.configure(bg=BG)
        win.geometry("780x600")
        win.transient(self.root)
        label(win, "搜索币名或代码，核对全名与 CoinGecko ID 后添加", size=12, bold=True).pack(anchor="w", padx=18, pady=14)
        searchbar = tk.Frame(win, bg=BG)
        searchbar.pack(fill="x", padx=18)
        query = tk.Entry(searchbar, bg=PANEL, fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 12), relief="flat")
        query.pack(side="left", fill="x", expand=True, ipady=6)
        results = tk.Listbox(win, bg=PANEL, fg=TEXT, selectbackground="#326551", relief="flat",
            font=("Segoe UI", 10), height=7, exportselection=False)
        results.pack(fill="x", padx=18, pady=10)
        candidates, instruments = [], []
        options = tk.Frame(win, bg=BG)
        options.pack(fill="x", padx=18)
        label(options, "价格来源").pack(side="left")
        pair = ttk.Combobox(options, state="readonly", width=28, values=["综合价格 USD（跟随刷新设置）"])
        pair.set("综合价格 USD（跟随刷新设置）")
        pair.pack(side="left", padx=10)
        note = label(win, "正在加载 OKX 现货交易对…", color=MUTED, size=9)
        note.pack(anchor="w", padx=18, pady=5)
        def live():
            return win.winfo_exists()
        def pick(event=None):
            selected = results.curselection()
            if not selected or not live():
                return
            cid = candidates[selected[0]]["id"]
            matches = self.feed.verified_pairs(cid, instruments)
            pair["values"] = matches + ["综合价格 USD（跟随刷新设置）"]
            pair.current(0)
            note.config(text="已按项目唯一标识核验 OKX 对应资产，也可选择综合价格。" if matches else
                        "此项目尚无已核验且可用的 OKX 交易对，使用综合价格。")
        results.bind("<<ListboxSelect>>", pick)
        def loaded(rows):
            if live():
                instruments[:] = rows
                note.config(text="OKX 现货交易对已加载；搜索币种即可添加。")
                pick()
        def fail(text):
            if live():
                note.config(text=text, fg=AMBER)
                search_button.config(state="normal")
        self.background(self.feed.instruments, loaded, fail)
        def searched(rows):
            if not live():
                return
            candidates[:] = rows
            results.delete(0, "end")
            for c in rows:
                results.insert("end", f"{c['symbol'].upper():<10} {c['name']}   |   ID: {c['id']}")
            search_button.config(state="normal")
            note.config(text=f"找到 {len(rows)} 项，请选择准确币种。", fg=MUTED)
            if rows:
                results.selection_set(0)
                pick()
        def search():
            text = query.get().strip()
            if not text:
                return
            search_button.config(state="disabled")
            note.config(text="正在搜索…", fg=MUTED)
            self.background(lambda: self.feed.search(text), searched, fail)
        search_button = button(searchbar, "搜索", search, True)
        search_button.pack(side="right", padx=(10, 0))
        query.bind("<Return>", lambda e: search())
        def add():
            selected = results.curselection()
            if not selected:
                return
            c = candidates[selected[0]]
            coins = self.feed.snapshot()["coins"]
            if any(x["id"] == c["id"] for x in coins):
                note.config(text="该币已在自选列表中。", fg=AMBER)
                return
            chosen_pair = pair.get() if pair.get().endswith("-USDT") else ""
            coins.append({"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"], "pair": chosen_pair})
            try:
                self.feed.set_coins(coins)
            except (OSError, ValueError) as exc:
                messagebox.showerror("无法保存", str(exc), parent=win)
                return
            refresh()
            self.rebuild()
            note.config(text="已添加，行情将自动加载。", fg=GREEN)
        button(options, "添加到自选", add, True).pack(side="right")
        label(win, "当前关注（可选中后删除或调整顺序）", bold=True).pack(anchor="w", padx=18, pady=(18, 6))
        current = tk.Listbox(win, bg=PANEL, fg=TEXT, selectbackground="#326551", relief="flat",
            font=("Segoe UI", 10), height=7, exportselection=False)
        current.pack(fill="both", expand=True, padx=18)
        def refresh(index=None):
            current.delete(0, "end")
            for c in self.feed.snapshot()["coins"]:
                current.insert("end", f"{c['symbol']:<10} {c['name']}  |  {c['pair'] or '综合价格 USD'}")
            if index is not None and current.size():
                current.selection_set(min(index, current.size() - 1))
        def change(delta=None):
            selection = current.curselection()
            if not selection:
                return
            i = selection[0]
            coins = self.feed.snapshot()["coins"]
            if delta is None:
                coins.pop(i)
            else:
                j = i + delta
                if j < 0 or j >= len(coins):
                    return
                coins[i], coins[j] = coins[j], coins[i]
                i = j
            self.feed.set_coins(coins)
            refresh(i)
            self.rebuild()
        actions = tk.Frame(win, bg=BG)
        actions.pack(fill="x", padx=18, pady=14)
        button(actions, "删除所选", change).pack(side="left")
        button(actions, "上移", lambda: change(-1)).pack(side="left", padx=8)
        button(actions, "下移", lambda: change(1)).pack(side="left")
        button(actions, "完成", win.destroy, True).pack(side="right")
        refresh()
        query.focus_set()

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("窗口设置")
        win.configure(bg=BG)
        win.geometry("540x455")
        win.transient(self.root)
        label(win, "窗口透明度", bold=True).pack(anchor="w", padx=20, pady=(16, 0))
        tk.Scale(win, from_=55, to=100, orient="horizontal", variable=self.opacity, bg=BG,
            fg=TEXT, highlightthickness=0, troughcolor=LINE, command=lambda v:
            self.root.attributes("-alpha", float(v) / 100)).pack(fill="x", padx=20)
        tk.Checkbutton(win, text="始终置顶", variable=self.topmost, bg=BG, fg=TEXT,
            selectcolor=PANEL, activebackground=BG, activeforeground=TEXT,
            command=lambda: self.root.attributes("-topmost", self.topmost.get())).pack(anchor="w", padx=20)
        refresh_row = tk.Frame(win, bg=BG)
        refresh_row.pack(fill="x", padx=20, pady=(14, 0))
        label(refresh_row, "综合数据自动刷新间隔（分钟）", size=9).pack(side="left")
        interval = ttk.Combobox(refresh_row, state="readonly", width=8, values=INTERVALS)
        interval.set(str(self.feed.interval // 60))
        interval.pack(side="right")
        estimate = label(win, color=GREEN, size=9)
        estimate.pack(anchor="w", padx=20, pady=(6, 0))
        def estimate_usage(event=None):
            count = 31 * 24 * 60 // int(interval.get())
            estimate.config(text=f"31天自动刷新约 {count:,} 次；10,000次额度余量约 {10000-count:,} 次。")
        interval.bind("<<ComboboxSelected>>", estimate_usage)
        estimate_usage()
        label(win, "余量还需扣除手动刷新、搜索及账户其他工具调用。", size=8, color=MUTED).pack(anchor="w", padx=20)
        label(win, "CoinGecko Demo API Key（可选，本机加密保存）", size=9).pack(anchor="w", padx=20, pady=(14, 5))
        key = tk.Entry(win, show="●", bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat")
        key.pack(fill="x", padx=20, ipady=6)
        key.insert(0, self.feed.key)
        label(win, "现在可直接使用；若遇到限流，可填写自己的免费 Demo Key。", size=8, color=MUTED).pack(anchor="w", padx=20, pady=6)
        def save():
            try:
                if key.get().strip() != self.feed.key:
                    self.feed.set_key(key.get().strip())
                self.feed.set_interval(int(interval.get()))
                self.save_settings()
            except OSError:
                messagebox.showerror("无法保存", "请检查本地目录写入权限。", parent=win)
                return
            win.destroy()
        button(win, "保存", save, True).pack(anchor="e", padx=20, pady=12)

    def save_settings(self):
        self.feed.settings.update({"geometry": self.root.geometry(), "opacity": self.opacity.get(), "topmost": self.topmost.get()})
        self.feed.persist()

    def close(self):
        self.save_settings()
        self.feed.close()
        self.alive = False
        if self._tick_job:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        self.diagnostics.close()
        if self.orbit:
            self.orbit.dispose()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default=str(BASE / "state"))
    parser.add_argument("--smoke-seconds", type=int, default=0)
    parser.add_argument("--smoke-report")
    args = parser.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    # One normal instance prevents duplicate market polling and quota usage.
    mutex = None
    if not args.smoke_seconds:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel.CreateMutexW(None, False, "Local\\AduCryptoWatchDesktopV1")
        if ctypes.get_last_error() == 183:
            ctypes.windll.user32.MessageBoxW(None, "行情窗已在运行，请查看桌面的发光圆球。", APP_TITLE, 0)
            return
    root = tk.Tk()
    feed = Feed(args.state_dir)
    app = WatchWindow(root, feed)
    feed.start()
    if args.smoke_seconds:
        def finish():
            snap = feed.snapshot()
            report = {"coins": [c["symbol"] for c in snap["coins"]], "price_count": len(snap["prices"]),
                "market_count": len(snap["market"]), "price_status": snap["price_status"],
                "market_status": snap["market_status"], "alpha": root.attributes("-alpha"),
                "topmost": bool(root.attributes("-topmost")), "table_width": app.table.winfo_width(),
                "table_requested_width": app.table.winfo_reqwidth(), "row_count": len(app.rows),
                "prices_fresh": all(not age_is_stale(p.get("ts"), time.time(), 20) for p in snap["prices"].values()),
                "market_fresh": all(not age_is_stale(p.get("ts"), time.time(), 900) for p in snap["market"].values())}
            if args.smoke_report:
                from market import atomic_json
                atomic_json(args.smoke_report, report)
            app.close()
        root.after(args.smoke_seconds * 1000, finish)
    root.mainloop()
    if mutex:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        (BASE / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "启动失败：" + type(exc).__name__ + "\n请查看程序目录中的 startup-error.log。", APP_TITLE, 16)
        raise
