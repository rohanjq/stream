"""Cairo drawing helpers for the overlay panels — same visual layout/colors as
the Smelter version's React components, so the two are a fair side-by-side.
"""
import cairo

COLORS = {
    "panel_bg": (0x0d / 255, 0x11 / 255, 0x17 / 255, 0.87),
    "panel_border": (0x30 / 255, 0x36 / 255, 0x3d / 255, 1),
    "fg": (0xe6 / 255, 0xed / 255, 0xf3 / 255, 1),
    "mut": (0x7d / 255, 0x85 / 255, 0x90 / 255, 1),
    "accent": (0x58 / 255, 0xa6 / 255, 0xff / 255, 1),
    "ai": (0x3f / 255, 0xb9 / 255, 0x50 / 255, 1),
    "gold": (0xe6 / 255, 0xc0 / 255, 0x4c / 255, 1),
    "bar_bg": (0x21 / 255, 0x26 / 255, 0x2d / 255, 1),
}

BAR_COLORS = [COLORS["accent"], COLORS["ai"], COLORS["gold"]]


def rgba(ctx, c):
    ctx.set_source_rgba(*c)


def rounded_rect(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -90 * 0.01745329, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, 90 * 0.01745329)
    ctx.arc(x + r, y + h - r, r, 90 * 0.01745329, 180 * 0.01745329)
    ctx.arc(x + r, y + r, r, 180 * 0.01745329, 270 * 0.01745329)
    ctx.close_path()


def panel(ctx, x, y, w, h, title):
    rounded_rect(ctx, x, y, w, h, 14)
    rgba(ctx, COLORS["panel_bg"])
    ctx.fill_preserve()
    rgba(ctx, COLORS["panel_border"])
    ctx.set_line_width(1)
    ctx.stroke()

    ctx.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(22)
    rgba(ctx, COLORS["fg"])
    ctx.move_to(x + 18, y + 34)
    ctx.show_text(title)


def text(ctx, x, y, s, size=15, color=COLORS["fg"], bold=False):
    ctx.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL,
                          cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
    rgba(ctx, color)
    ctx.move_to(x, y)
    ctx.show_text(s)


def draw_badge(ctx, layout):
    rounded_rect(ctx, 20, 20, 260, 40, 8)
    rgba(ctx, (0x0d / 255, 0x11 / 255, 0x17 / 255, 0.8))
    ctx.fill()
    text(ctx, 32, 46, f"layout: {layout}", size=16, color=COLORS["ai"], bold=True)


def draw_logs(ctx, w, h, logs):
    px, py, pw, ph = w - 450, 30, 420, 340
    panel(ctx, px, py, pw, ph, "Event Log")
    if not logs:
        text(ctx, px + 18, py + 70, "Waiting for events…", size=16, color=COLORS["mut"])
        return
    y = py + 68
    for entry in logs:
        text(ctx, px + 18, y, entry["ts"], size=15, color=COLORS["accent"])
        text(ctx, px + 90, y, entry["text"], size=15, color=COLORS["fg"])
        y += 32


def draw_poll(ctx, w, h, poll):
    if not poll:
        return
    px, py, pw, ph = w - 490, 30, 460, 320
    panel(ctx, px, py, pw, ph, "")
    text(ctx, px + 18, py + 40, poll["question"], size=22, color=COLORS["fg"], bold=True)

    total = max(1, sum(o["votes"] for o in poll["options"]))
    y = py + 80
    for i, o in enumerate(poll["options"]):
        pct = round(o["votes"] / total * 100)
        text(ctx, px + 18, y, o["label"], size=16, color=COLORS["fg"])
        text(ctx, px + 260, y, f"{o['votes']} votes · {pct}%", size=15, color=COLORS["mut"])
        bar_y = y + 14
        bar_w = 420
        rounded_rect(ctx, px + 18, bar_y, bar_w, 14, 7)
        rgba(ctx, COLORS["bar_bg"])
        ctx.fill()
        fill_w = max(6, round(bar_w * pct / 100))
        rounded_rect(ctx, px + 18, bar_y, fill_w, 14, 7)
        rgba(ctx, BAR_COLORS[i % len(BAR_COLORS)])
        ctx.fill()
        y += 60


def draw_leaderboard(ctx, w, h, entries):
    px, py, pw, ph = w - 450, 30, 420, 400
    panel(ctx, px, py, pw, ph, "Leaderboard")
    if not entries:
        text(ctx, px + 18, py + 70, "No entries yet", size=16, color=COLORS["mut"])
        return
    medals = [COLORS["gold"], COLORS["fg"], (0xcd / 255, 0x7f / 255, 0x32 / 255, 1)]
    y = py + 70
    for i, e in enumerate(entries):
        color = medals[i] if i < 3 else COLORS["mut"]
        text(ctx, px + 18, y, f"#{i+1}", size=18, color=color, bold=True)
        text(ctx, px + 60, y, e["name"], size=17, color=COLORS["fg"])
        text(ctx, px + 340, y, str(e["score"]), size=17, color=COLORS["ai"], bold=True)
        y += 40


def draw_scene(ctx, w, h, snapshot):
    draw_badge(ctx, snapshot["layout"])
    layout = snapshot["layout"]
    if layout == "logs":
        draw_logs(ctx, w, h, snapshot["logs"])
    elif layout == "poll":
        draw_poll(ctx, w, h, snapshot["poll"])
    elif layout == "leaderboard":
        draw_leaderboard(ctx, w, h, snapshot["leaderboard"])
