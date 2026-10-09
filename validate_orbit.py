"""Offline interaction acceptance; never modifies production watchlist or keys."""
import json
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace

from app import WatchWindow, BG, PANEL, TEXT, MUTED, GREEN, RED
from market import Feed, BASE, atomic_json

checks = {}
temp = tempfile.TemporaryDirectory()
feed = Feed(temp.name)
root = tk.Tk()
app = WatchWindow(root, feed)
orb = app.orbit
root.update()
orb.pointer = lambda: (orb.x+52, orb.y+92)


def check(name, value):
    checks[name] = bool(value)


try:
    check("startup_panel_collapsed", root.state() == "withdrawn" and not orb.visible)
    check("startup_orb_visible", orb.ball.winfo_viewable())
    check("default_panel_opaque", float(root.attributes("-alpha")) == 1)
    orb.enter()
    root.update()
    check("hover_opens_preview", orb.visible and not orb.pinned and root.winfo_viewable())
    orb.pointer = lambda: (root.winfo_rootx()+100, root.winfo_rooty()+100)
    orb.outside_since = time.monotonic()-1
    orb.poll()
    check("moving_into_panel_keeps_open", orb.visible)
    orb.pointer = lambda: (-10000, -10000)
    orb.outside_since = time.monotonic()-1
    orb.poll()
    check("leaving_preview_hides", not orb.visible and root.state() == "withdrawn")
    orb.pointer = lambda: (orb.x+52, orb.y+92)
    orb.enter()
    event = SimpleNamespace(x_root=orb.x+44, y_root=orb.y+44)
    orb.press(event)
    orb.release(event)
    root.update()
    check("click_pins", orb.visible and orb.pinned)
    orb.pointer = lambda: (-10000, -10000)
    orb.outside_since = time.monotonic()-1
    orb.poll()
    check("pinned_survives_pointer_leave", orb.visible)
    orb.pointer = lambda: (orb.x+52, orb.y+92)
    orb.press(event)
    orb.release(event)
    orb.enter()
    check("second_click_collapses_without_reopening", not orb.visible and orb.suppressed)
    orb.pointer = lambda: (-10000, -10000)
    orb.poll()
    orb.enter()
    check("reentry_restores_hover", orb.visible and not orb.pinned)
    before = (orb.x, orb.y)
    orb.press(SimpleNamespace(x_root=orb.x+44, y_root=orb.y+44))
    orb.motion(SimpleNamespace(x_root=orb.x+14, y_root=orb.y+74))
    orb.release(event)
    root.update()
    check("drag_moves_orb_without_pin", (orb.x, orb.y) != before and not orb.pinned)
    check("drag_position_saved", Feed(temp.name).settings.get("orb_position") == [orb.x, orb.y])
    app.open_settings()
    root.update()
    orb.pointer = lambda: (-10000, -10000)
    orb.outside_since = time.monotonic()-1
    orb.poll()
    check("dialog_keeps_preview_open", orb.visible)
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and child != orb.ball:
            child.destroy()
    orb.show(pin=True)
    root.update()
    check("table_fits", app.table.winfo_reqwidth() <= app.canvas.winfo_width())
    root.geometry("1180x490")
    root.update()
    header_y = app.header.winfo_rooty()
    first_row_y = app.rows[feed.coins[0]["id"]][0][0].winfo_rooty()
    app.canvas.yview_moveto(1)
    root.update()
    check("header_fixed_during_scroll", app.header.winfo_rooty() == header_y)
    check("only_data_rows_scroll", app.rows[feed.coins[0]["id"]][0][0].winfo_rooty() < first_row_y)
    import tkinter.font as tkfont
    check("all_seven_headers_bold", len(app.header.winfo_children()) == 7 and all(
        tkfont.Font(font=w.cget("font")).actual("weight") == "bold" for w in app.header.winfo_children()))
    app.canvas.yview_moveto(0)
    root.geometry("1180x800")
    root.update()
    check("header_and_body_columns_aligned", all(abs(app.header.grid_bbox(c,0)[0] - app.table.grid_bbox(c,0)[0]) <= 1 for c in range(7)))
    last_cell = app.rows[feed.coins[-1]["id"]][0][0].master
    check("default_six_rows_fully_visible", last_cell.winfo_rooty()+last_cell.winfo_height() <= app.canvas.winfo_rooty()+app.canvas.winfo_height())
    left, top, right, bottom = __import__("orbit").work_area(root)
    check("panel_inside_workarea", root.winfo_rootx() >= left and root.winfo_rooty() >= top
          and root.winfo_rootx()+root.winfo_width() <= right and root.winfo_rooty()+root.winfo_height() <= bottom)
    check("six_assets_retained", len(app.rows) == 6)
    check("refresh_button_retained", app.refresh_button.winfo_exists())
    check("countdown_retained", "下次刷新" in app.countdown.cget("text"))
    from PIL import ImageGrab
    x, y = orb.ball.winfo_rootx(), orb.ball.winfo_rooty()
    ImageGrab.grab((x, y, x+orb.SIZE, y+orb.SIZE)).save(BASE/"validation"/"orb-preview.png")
    check("wool_image_loaded_with_alpha", orb.surface.image.mode == "RGBA" and orb.surface.alpha.getextrema() == (0, 255))
    check("transparent_corner_excluded", not orb.surface.hit(0, 0))
    check("wool_body_hit_target", orb.surface.hit(52, 92))
    # Use the previously validated public snapshot for a visual layout preview.
    from market import load_json
    saved = load_json(BASE/"state"/"market-cache.json", {})
    feed.market = saved.get("market", {})
    feed.market_received = saved.get("received", 0)
    app.tick()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab((x, y, x+root.winfo_width(), y+root.winfo_height())).save(BASE/"validation"/"orbit-panel-preview.png")
    app.collapse_panel()
    check("panel_collapse_control", not orb.visible)
    orb.show(pin=True)
    orb.collapse()
    check("escape_collapse_handler", not orb.visible and not orb.pinned)
finally:
    atomic_json(BASE/"validation"/"orbit-ui.json", {"checks": checks, "all_passed": bool(checks) and all(checks.values())})
    app.close()
    temp.cleanup()
print(json.dumps(checks, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
