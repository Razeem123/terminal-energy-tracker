#!/usr/bin/env python3
"""Terminal energy tracker — compact sidebar with scaled title rows."""

import argparse
import curses
import sys
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

from sensors.cpu_sensor import AMDRAPLSensor
from sensors.gpu_sensor import NVGpuSensor
from sensors.ram_sensor import DRAMSensor
from sensors.storage_sensor import StorageSensor
from sensors.base import PowerReading


# ---------------------------------------------------------------------------
# History buffer
# ---------------------------------------------------------------------------

class HistoryBuffer:
    def __init__(self, maxlen: int = 300):
        self._data: deque = deque(maxlen=maxlen)

    def push(self, value: float):
        self._data.append(value)

    def values(self) -> List[float]:
        return list(self._data)

    def last(self) -> float:
        return self._data[-1] if self._data else 0.0


# ---------------------------------------------------------------------------
# Component reader
# ---------------------------------------------------------------------------

class ComponentReader:
    def __init__(self, polling_ms: int = 1000):
        self.polling_ms = polling_ms
        self.gpus: List[NVGpuSensor] = []
        self.cpu: Optional[AMDRAPLSensor] = None
        self.ram: Optional[DRAMSensor] = None
        self.storage: Optional[StorageSensor] = None
        self._cumulative_energy_wh = 0.0
        self._last_total_watts = 0.0
        self._last_reading_time = 0.0
        self._init_sensors()

    def _init_sensors(self):
        self.gpus = [NVGpuSensor()]
        self.cpu = AMDRAPLSensor()
        self.ram = DRAMSensor()
        self.storage = StorageSensor()

        gpu_count = sum(len(g._gpus) for g in self.gpus if g.available())
        print(f"[Sensors] GPUs:{gpu_count} CPU:{self.cpu.available()} "
              f"RAM:{self.ram.available()} "
              f"Storage:{self.storage.available()}",
              file=sys.stderr)

    def read(self) -> Dict[str, PowerReading]:
        """Read all sensors — returns all components even if idle (0W)."""
        result: Dict[str, PowerReading] = {}
        for gpu in self.gpus:
            if gpu.available():
                for r in gpu.read():
                    result[r.component] = r
        if self.cpu and self.cpu.available():
            r = self.cpu.read()
            if r:
                result[r.component] = r
        if self.ram and self.ram.available():
            r = self.ram.read()
            if r:
                result[r.component] = r
        for r in self.storage.read():
            result[r.component] = r

        total_watts = sum(r.power_watts for r in result.values())
        now = time.time()
        if self._last_reading_time > 0 and result:
            dt_h = (now - self._last_reading_time) / 3600.0
            if dt_h > 0:
                avg_watts = (total_watts + self._last_total_watts) / 2.0
                self._cumulative_energy_wh += avg_watts * dt_h
        self._last_total_watts = total_watts
        self._last_reading_time = now

        return result

    def get_total_energy_wh(self) -> float:
        return self._cumulative_energy_wh

    def get_cpu_temp(self) -> Optional[float]:
        return self.cpu.get_temperature() if self.cpu else None

    def shutdown(self):
        for gpu in self.gpus:
            gpu.shutdown()


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

C_GREEN = 1
C_YELLOW = 2
C_ORANGE = 3
C_RED = 4
C_CYAN = 5
C_BLUE = 6
C_WHITE = 7
C_MAGENTA = 8
C_BLACK_BG = 9


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
    curses.init_pair(C_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(C_ORANGE, 166, -1)
    curses.init_pair(C_RED, curses.COLOR_RED, -1)
    curses.init_pair(C_CYAN, curses.COLOR_CYAN, -1)
    curses.init_pair(C_BLUE, curses.COLOR_BLUE, -1)
    curses.init_pair(C_WHITE, curses.COLOR_WHITE, -1)
    curses.init_pair(C_MAGENTA, curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_BLACK_BG, curses.COLOR_BLACK, curses.COLOR_WHITE)


def power_color(watts: float) -> int:
    if watts < 50:
        return C_GREEN
    elif watts < 150:
        return C_YELLOW
    elif watts < 300:
        return C_ORANGE
    return C_RED


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_energy(wh: float) -> str:
    if wh >= 1000:
        return f"{wh / 1000:.2f} kWh"
    return f"{wh:.3f} Wh"


def format_cost(rupees: float) -> str:
    if rupees >= 1:
        return f"Rs.{rupees:.2f}"
    return f"Rs.{rupees:.1f}"


# ---------------------------------------------------------------------------
# Safe drawing
# ---------------------------------------------------------------------------

def safe_addch(win, y: int, x: int, ch, attr: int = 0):
    try:
        max_y, max_x = win.getmaxyx()
        if 0 <= y < max_y and 0 <= x < max_x:
            win.addch(y, x, ch, attr)
    except curses.error:
        pass


def draw_line(win, y: int, x: int, text: str, attr: int = 0):
    try:
        max_y, max_x = win.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        visible = min(len(text), max_x - x - 1)
        if visible > 0:
            win.addstr(y, x, text[:visible], attr)
    except curses.error:
        pass


def clear_line(win, y: int, x: int, length: int):
    try:
        max_y, max_x = win.getmaxyx()
        if y < 0 or y >= max_y or x < 0:
            return
        actual_len = min(length, max_x - x - 1)
        if actual_len > 0:
            win.addstr(y, x, " " * actual_len)
    except curses.error:
        pass


# ---------------------------------------------------------------------------
# Block-text renderer (2x / 3x via Unicode half-blocks)
# ---------------------------------------------------------------------------

# 5x5 font for digits, common letters, and symbols we need
_FONT = {
    '0': [" oo ", "o  o", "o  o", "o  o", " oo "],
    '1': ["  o ", " oo ", "  o ", "  o ", " ooo"],
    '2': [" oo ", "o  o", "   o", "  o ", "oooo"],
    '3': [" oo ", "    o", "  oo ", "    o", " oo "],
    '4': ["o  o", "o  o", "oooo", "   o", "   o"],
    '5': ["oooo", "o   ", "ooo ", "   o ", "ooo "],
    '6': [" ooo", "o   ", "ooo ", "o  o", " ooo"],
    '7': ["oooo", "   o", "  o ", " o  ", "o   "],
    '8': [" ooo", "o  o", " ooo", "o  o", " ooo"],
    '9': [" ooo", "o  o", " ooo", "   o ", " ooo "],
    'A': [" ooo ", "o   o", "ooooo", "o   o", "o   o"],
    'B': ["oooo ", "o   o", "oooo ", "o   o", "oooo "],
    'C': [" oooo", "o   o", "o   o", "o   o", " oooo"],
    'D': ["oooo ", "o   o", "o   o", "o   o", "oooo "],
    'E': ["ooooo", "o   ", "oooo ", "o   ", "ooooo"],
    'F': ["ooooo", "o   ", "oooo ", "o   ", "o   "],
    'G': [" oooo", "o   o", "o  oo", "o  o ", " oooo"],
    'H': ["o   o", "o   o", "ooooo", "o   o", "o   o"],
    'I': ["ooooo", "  o  ", "  o  ", "  o  ", "ooooo"],
    'J': ["ooooo", "   o ", "   o ", "o  o ", " oo o"],
    'K': ["o  o ", "o o  ", "ooo  ", "o o  ", "o  o "],
    'L': ["o   ", "o   ", "o   ", "o   ", "ooooo"],
    'M': ["o   o", "oo oo", "o o o", "o   o", "o   o"],
    'N': ["o   o", "oo  o", "o o o", "o  oo", "o   o"],
    'O': [" ooo ", "o   o", "o   o", "o   o", " ooo "],
    'P': ["oooo ", "o   o", "oooo ", "o   ", "o   "],
    'Q': [" ooo ", "o  oo", "o o o", "o  o ", " oo o"],
    'R': ["oooo ", "o  o ", "oooo ", "o  o ", "o   o"],
    'S': [" oooo", "o   o", " ooo ", "    o", "oooo "],
    'T': ["ooooooo", "  o  ", "  o  ", "  o  ", "  o  "],
    'U': ["o   o", "o   o", "o   o", "o   o", " ooo "],
    'V': ["o   o", "o   o", "o   o", " o o ", "  o  "],
    'W': ["o   o", "o   o", "o o o", "oo oo", "o   o"],
    'X': ["o   o", " o o ", "  o  ", " o o ", "o   o"],
    'Y': ["o   o", " o o ", "  o  ", "  o  ", "  o  "],
    'Z': ["ooooo", "   o ", "  o  ", " o   ", "ooooo"],
    '.': ["     ", "     ", "     ", "  o  ", "  o  "],
    ',': ["     ", "     ", "     ", "  o  ", " o   "],
    ':': ["  o  ", "     ", "  o  ", "     ", "  o  "],
    '-': ["     ", "     ", "ooooo", "     ", "     "],
    '+': ["  o  ", "  o  ", "ooooo", "  o  ", "  o  "],
    '/': ["  o  ", " o o ", "  oo  ", " o o ", "o   o"],
    '|': ["  o  ", "  o  ", "  o  ", "  o  ", "  o  "],
    '!': ["  o  ", "  o  ", "  o  ", "     ", "  o  "],
    '?': [" ooo ", "o  o ", "  oo  ", "  o  ", "     "],
    '%': ["o   o", "   o ", "  o  ", " o   ", "o   o"],
    '°': [" ooo ", "o   o", " ooo ", "     ", "     "],
    ' ': ["     ", "     ", "     ", "     ", "     "],
    '#': [" o o ", " o o ", "ooooo", " o o ", "ooooo"],
    '@': [" oo  ", "o  oo", "o oo ", "o  o ", " oo  "],
}

# Fallback for unknown chars
_FONT_DEFAULT = ["     ", "  o  ", "     ", "  o  ", "     "]


def _glyph_width(glyph):
    """Find the pixel width of a glyph (max span of ink pixels)."""
    all_first = []
    all_last = []
    for row in glyph:
        first = 0
        last = len(row) - 1
        for i, c in enumerate(row):
            if c == 'o':
                first = i
                break
        for i in range(len(row) - 1, -1, -1):
            if row[i] == 'o':
                last = i
                break
        if first <= last:
            all_first.append(first)
            all_last.append(last)
    if not all_first:
        return 5, 0, 4
    return max(all_last) - min(all_first) + 1, min(all_first), max(all_first)


def scale_text(text: str, scale_h: int, max_width: int = 0) -> List[str]:
    """Render text as pixel-art using █ and space.
    Each char is rendered at its full glyph width (variable, typically 5).
    scale_h = number of output rows (2 or 3).
    max_width = if set, center the output in this width.
    Returns list of scale_h strings."""
    if scale_h == 1:
        return [text]

    # Determine row bands (non-overlapping to preserve shape detail)
    if scale_h == 2:
        bands = [(0, 2), (3, 5)]  # top=rows 0-1, bottom=rows 3-4, skip row 2
    elif scale_h == 3:
        bands = [(0, 2), (2, 3), (3, 5)]  # top, middle, bottom
    else:
        bands = [(0, 5)] * scale_h

    # Build total pixel width
    total_px = 0
    for ch in text:
        glyph = _FONT.get(ch.upper(), _FONT_DEFAULT)
        w, _, _ = _glyph_width(glyph)
        total_px += w

    # For each output band, render all pixels
    result = []
    for band_start, band_end in bands:
        pixels = []
        for ch in text:
            glyph = _FONT.get(ch.upper(), _FONT_DEFAULT)
            w, first_col, last_col = _glyph_width(glyph)
            for col in range(first_col, last_col + 1):
                has_ink = False
                for r in range(band_start, min(band_end, len(glyph))):
                    if col < len(glyph[r]) and glyph[r][col] == 'o':
                        has_ink = True
                        break
                pixels.append("#" if has_ink else " ")
        line = "".join(pixels)
        if max_width > 0 and len(line) < max_width:
            pad = (max_width - len(line)) // 2
            line = " " * pad + line + " " * (max_width - len(line) - pad)
        result.append(line)

    return result


# ---------------------------------------------------------------------------
# Compact panel layout
# ---------------------------------------------------------------------------

PANEL_MIN_W = 14
PANEL_H = 3  # title + power/bar + energy


def draw_panel_box(win, y: int, x: int, w: int, h: int, title: str, color: int):
    """Draw a compact panel box."""
    max_y, max_x = win.getmaxyx()
    if y + h > max_y or x + w > max_x:
        return

    for i in range(w):
        safe_addch(win, y, x + i, " ", curses.color_pair(C_BLACK_BG) | curses.A_BOLD)
    safe_addch(win, y, x, "+", curses.color_pair(color))
    safe_addch(win, y, x + w - 1, "+", curses.color_pair(color))

    title_text = f" {title} "
    draw_line(win, y, x + 2, title_text[:w - 4],
              curses.color_pair(color) | curses.A_BOLD | curses.A_REVERSE)

    for row in range(1, h - 1):
        safe_addch(win, y + row, x, "|", curses.color_pair(color))
        safe_addch(win, y + row, x + w - 1, "|", curses.color_pair(color))

    for i in range(w):
        safe_addch(win, y + h - 1, x + i, "-", curses.color_pair(color))
    safe_addch(win, y + h - 1, x, "+", curses.color_pair(color))
    safe_addch(win, y + h - 1, x + w - 1, "+", curses.color_pair(color))


# ---------------------------------------------------------------------------
# Sparkline (single line)
# ---------------------------------------------------------------------------

def draw_sparkline(data: List[float], width: int) -> str:
    if not data or width < 2:
        return " " * width

    step = max(1, len(data) // width)
    samples = data[::step][:width]
    if not samples:
        return " " * width

    min_v = min(samples)
    max_v = max(samples)
    rng = max_v - min_v if max_v > min_v else 1.0

    chars = " .:;+=*#@"
    line = []
    for val in samples:
        pct = (val - min_v) / rng if rng > 0 else 0.5
        idx = int(pct * (len(chars) - 1))
        line.append(chars[min(idx, len(chars) - 1)])
    return "".join(line[:width])


# ---------------------------------------------------------------------------
# Main TUI
# ---------------------------------------------------------------------------

def curses_main(stdscr, args):
    init_colors()
    curses.curs_set(0)
    stdscr.timeout(args.interval)  # match redraw rate to polling interval
    stdscr.scrollok(False)

    reader = ComponentReader(polling_ms=args.interval)
    history = HistoryBuffer(maxlen=500)
    for _ in range(10):
        history.push(0.0)

    session_start = time.time()
    paused = False
    paused_time = 0.0
    last_read_time = 0.0
    help_visible = False

    max_y, max_x = stdscr.getmaxyx()
    # Off-screen pad — erase is memory-only (no terminal I/O), then atomic blit to screen
    pad = curses.newpad(max_y + 1, max_x)
    pad_erase_count = 0  # track for resize

    try:
        while True:
            key = stdscr.getch()
            if key in (ord('q'), ord('Q'), 27):
                break
            elif key == ord('p'):
                paused = not paused
                if paused:
                    paused_time = time.time() - session_start
                else:
                    session_start = time.time() - paused_time
            elif key == ord('r'):
                paused = False
                paused_time = 0.0
                session_start = time.time()
            elif key == ord('h'):
                help_visible = not help_visible

            now = time.time()
            readings: Dict[str, PowerReading] = {}
            if not paused and now - last_read_time >= args.interval / 1000.0:
                readings = reader.read()
                total_power = sum(r.power_watts for r in readings.values())
                history.push(total_power)
                last_read_time = now

            # Stats
            elapsed = (now - session_start) if not paused else paused_time
            total_power = sum(r.power_watts for r in readings.values()) if readings else history.last()
            total_energy_wh = reader.get_total_energy_wh()
            cost = total_energy_wh * args.price / 1000.0
            max_power = max(history.values()) if history.values() else 0
            cpu_temp = reader.get_cpu_temp()

            max_y, max_x = stdscr.getmaxyx()
            # Handle resize — recreate pad if dimensions changed
            if max_y != pad_erase_count:
                try:
                    pad = curses.newpad(max_y + 1, max_x)
                    pad_erase_count = max_y
                except curses.error:
                    pass

            if max_y < 15 or max_x < 50:
                pad.erase()
                draw_line(pad, 0, 0, "Terminal too small! Need 50x15+.", curses.A_BOLD)
                pad.noutrefresh(0, 0, 0, 0, max_y - 1, max_x - 1)
                curses.doupdate()
                continue

            # Erase pad (memory-only, zero terminal I/O) then draw
            pad.erase()
            components: List[PowerReading] = list(readings.values())

            # Separate primary (GPU/CPU/RAM) from storage
            primary = [c for c in components if "Storage" not in c.component]
            storage = [c for c in components if "Storage" in c.component]

            # ---- Layout positions ----
            # Row 0-1:   Header (2x scaled) — "POWER XXXX W"
            # Row 2-4:   Info (3x scaled) — energy / cost / time
            # Row 5:     Detail line — cost, time, temp
            # Row 6-8:   Primary panels (GPU, CPU, RAM)
            # Row 9-11:  Storage panels (if any)
            # Row 12+:   Chart + footer

            # ---- Header: 2x scaled power display ----
            header_y = 0
            pw_str = f"{total_power:.0f}W"
            hdr_text = f"POWER {pw_str}"
            hdr_lines = scale_text(hdr_text, 2)
            hdr_attr = curses.color_pair(power_color(total_power)) | curses.A_BOLD
            for i, line in enumerate(hdr_lines):
                if header_y + i < max_y:
                    draw_line(pad, header_y + i, 2, line[:max_x - 3], hdr_attr)

            # Also show max power (small)
            max_str = f"max:{max_power:.0f}W"
            draw_line(pad, header_y, max_x - len(max_str) - 1, max_str, curses.A_DIM)

            # ---- Info row: 3x scaled ----
            info_y = header_y + 2

            # Pick the most interesting number to display big
            if total_energy_wh >= 100:
                info_big = f"{total_energy_wh / 1000:.2f}kWh"
            else:
                info_big = f"{total_energy_wh:.1f}Wh"
            info_lines = scale_text(info_big, 3)
            info_attr = curses.color_pair(C_CYAN) | curses.A_BOLD
            for i, line in enumerate(info_lines):
                if info_y + i < max_y:
                    draw_line(pad, info_y + i, 2, line[:max_x - 3], info_attr)

            # Small text below big number: cost, time, temp
            detail_y = info_y + 3  # after 3-row info block
            cost_str = format_cost(cost)
            time_str = format_time(elapsed)
            temp_str = f"{cpu_temp:.0f}C" if cpu_temp else "--C"
            detail_line = f"  {cost_str}  {time_str}  CPU:{temp_str}"
            draw_line(pad, detail_y, 2, detail_line,
                     curses.color_pair(C_WHITE) | curses.A_BOLD)

            # ---- Primary panels (GPU, CPU, RAM) ----
            panel_y = detail_y + 1  # after detail line
            panel_w = max(PANEL_MIN_W, (max_x - 6) // max(len(primary), 1))
            panel_w = min(panel_w, 24)
            num_cols = max(1, (max_x - 2) // panel_w)

            for idx, comp in enumerate(primary):
                col = idx % num_cols
                px = 1 + col * panel_w
                py = panel_y

                if px + panel_w > max_x or py + PANEL_H > max_y:
                    continue

                comp_color = power_color(comp.power_watts)
                draw_panel_box(pad, py, px, panel_w, PANEL_H,
                               comp.component[:panel_w - 4], comp_color)

                cx = px + 2
                cw = panel_w - 4
                if cw < 4:
                    continue

                pw = f"{comp.power_watts:5.0f}W"
                draw_line(pad, py + 1, cx, pw, curses.color_pair(comp_color))
                bar_w = max(2, cw - len(pw) - 2)
                ratio = min(comp.power_watts / 600.0, 1.0)
                filled = int(bar_w * ratio)
                bar = "#" * filled + "-" * (bar_w - filled)
                draw_line(pad, py + 1, cx + len(pw) + 1, f"|{bar[:bar_w]}")

                energy_str = format_energy(comp.energy_wh)
                draw_line(pad, py + 2, cx, energy_str)

                if "CPU" in comp.component and cpu_temp is not None and cw > 14:
                    tc = C_GREEN if cpu_temp < 60 else (C_YELLOW if cpu_temp < 80 else C_RED)
                    draw_line(pad, py + 2, cx + len(energy_str) + 1,
                             f" {cpu_temp:.0f}C", curses.color_pair(tc))

            # ---- Storage panels (separate row) ----
            storage_y = panel_y + PANEL_H + 1
            if storage:
                stor_num_cols = max(1, (max_x - 2) // panel_w)
                for idx, comp in enumerate(storage):
                    col = idx % stor_num_cols
                    px = 1 + col * panel_w
                    py = storage_y

                    if px + panel_w > max_x or py + PANEL_H > max_y:
                        continue

                    comp_color = power_color(comp.power_watts)
                    draw_panel_box(pad, py, px, panel_w, PANEL_H,
                                   comp.component[:panel_w - 4], comp_color)

                    cx = px + 2
                    cw = panel_w - 4
                    if cw < 4:
                        continue

                    pw = f"{comp.power_watts:5.0f}W"
                    draw_line(pad, py + 1, cx, pw, curses.color_pair(comp_color))
                    bar_w = max(2, cw - len(pw) - 2)
                    ratio = min(comp.power_watts / 600.0, 1.0)
                    filled = int(bar_w * ratio)
                    bar = "#" * filled + "-" * (bar_w - filled)
                    draw_line(pad, py + 1, cx + len(pw) + 1, f"|{bar[:bar_w]}")

                    energy_str = format_energy(comp.energy_wh)
                    draw_line(pad, py + 2, cx, energy_str)

                last_row = storage_y + PANEL_H
            else:
                last_row = panel_y + PANEL_H

            # ---- Chart (single row, pinned just above footer) ----
            chart_y = max_y - 2  # one row above footer
            chart_w = max_x - 4
            spark = draw_sparkline(history.values(), chart_w)
            if history.values():
                label = f"MAX {max(history.values()):.0f}W"
                draw_line(pad, chart_y, 1, label, curses.A_DIM)
                draw_line(pad, chart_y, len(label) + 1,
                         spark[len(label):chart_w - len(label) - 1], curses.color_pair(C_BLUE))
            else:
                draw_line(pad, chart_y, 1, spark, curses.color_pair(C_BLUE))

            # ---- Footer ----
            footer_y = max_y - 1
            if help_visible:
                footer_text = " p:pause  r:reset  h:hide  q:quit  esc:quit"
            else:
                footer_text = " p:pause  r:reset  h:help  q/esc:quit"

            for i in range(max_x):
                safe_addch(pad, footer_y, i, " ", curses.color_pair(C_BLACK_BG) | curses.A_BOLD)
            draw_line(pad, footer_y, 2, footer_text[:max_x - 3],
                     curses.color_pair(C_CYAN) | curses.A_BOLD)

            # Blit pad to screen (atomic diff — no flicker)
            pad.noutrefresh(0, 0, 0, 0, max_y - 1, max_x - 1)
            curses.doupdate()

    except KeyboardInterrupt:
        pass
    finally:
        reader.shutdown()
        curses.curs_set(1)


def main():
    parser = argparse.ArgumentParser(description="Terminal power tracker — compact sidebar.")
    parser.add_argument("-i", "--interval", type=int, default=1000, help="Polling interval in ms")
    parser.add_argument("-p", "--price", type=float, default=90.0, help="Electricity price per kWh in PKR")
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    args = parser.parse_args()

    curses.wrapper(lambda stdscr: curses_main(stdscr, args))


if __name__ == "__main__":
    main()
