#!/usr/bin/env python3
"""Terminal energy tracker — compact sidebar with scaled title rows."""

import argparse
import curses
import json
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sensors.cpu_sensor import AMDRAPLSensor
from sensors.gpu_sensor import NVGpuSensor
from sensors.ram_sensor import DRAMSensor
from sensors.storage_sensor import StorageSensor
from sensors.base import PowerReading

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".config" / "ecost"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_cost_per_kwh() -> float:
    cfg = load_config()
    if "cost_per_kwh" in cfg:
        return float(cfg["cost_per_kwh"])

    # First run — ask user
    print()
    print("  Welcome to ecost! Let's set your energy cost.")
    print()

    while True:
        raw = input("  Cost per 1 kWh at your location? (e.g. 0.12 for $0.12, or 90 for Rs.90): ")
        try:
            cost = float(raw)
            if cost < 0:
                print("  Please enter a positive number.")
                continue
            save_config({"cost_per_kwh": cost})
            print(f"  Saved! Your cost: {cost}/kWh")
            print()
            time.sleep(1)
            return cost
        except ValueError:
            print("  Please enter a valid number.")


def get_currency() -> str:
    cfg = load_config()
    return cfg.get("currency", "$")


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


def format_cost(amount: float, currency: str = "$") -> str:
    if amount >= 1:
        return f"{currency}{amount:.2f}"
    return f"{currency}{amount:.1f}"


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


# Compact panel layout

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
    needs_redraw = True  # only redraw when data actually changes

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
            elif key == ord('c'):
                # Re-set cost — show prompt briefly
                pad.erase()
                draw_line(pad, max_y // 2, 2, "Enter new cost per kWh (or press Esc to cancel):",
                         curses.color_pair(C_CYAN) | curses.A_BOLD)
                draw_line(pad, max_y // 2 + 1, 4, f"Current: {args.cost_per_kwh}/kWh",
                         curses.color_pair(C_WHITE))
                pad.noutrefresh(0, 0, 0, 0, max_y - 1, max_x - 1)
                curses.doupdate()

                # Temporarily enable echoing and cursor for input
                curses.echo()
                curses.curs_set(1)
                stdscr.nodelay(False)
                try:
                    raw = stdscr.getstr(max_y // 2 + 2, 4, 20).decode().strip()
                    if raw:
                        new_cost = float(raw)
                        if new_cost >= 0:
                            args.cost_per_kwh = new_cost
                            save_config({"cost_per_kwh": new_cost})
                            needs_redraw = True
                except ValueError:
                    pass
                finally:
                    curses.noecho()
                    curses.curs_set(0)
                    stdscr.timeout(args.interval)
                needs_redraw = True

            now = time.time()
            readings: Dict[str, PowerReading] = {}
            if not paused and now - last_read_time >= args.interval / 1000.0:
                readings = reader.read()
                total_power = sum(r.power_watts for r in readings.values())
                history.push(total_power)
                last_read_time = now
                needs_redraw = True  # data changed, redraw next frame

            # Stats
            elapsed = (now - session_start) if not paused else paused_time
            total_power = sum(r.power_watts for r in readings.values()) if readings else history.last()
            total_energy_wh = reader.get_total_energy_wh()
            cost = total_energy_wh * args.cost_per_kwh / 1000.0
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
            # Row 0:     Header — "POWER XXXX W"
            # Row 1:     Info — energy / cost / time
            # Row 2:     Detail line — cost, time, temp
            # Row 3-5:   Primary panels (GPU, CPU, RAM)
            # Row 6-8:   Storage panels (if any)
            # Row 9+:    Chart + footer

            # ---- Header: power display ----
            header_y = 0
            pw_str = f"POWER {total_power:.0f}W"
            hdr_attr = curses.color_pair(power_color(total_power)) | curses.A_BOLD | curses.A_REVERSE
            draw_line(pad, header_y, 2, pw_str[:max_x - 3], hdr_attr)

            # Also show max power (small)
            max_str = f"max:{max_power:.0f}W"
            draw_line(pad, header_y, max_x - len(max_str) - 1, max_str, curses.A_DIM)

            # ---- Info row: energy ----
            info_y = header_y + 1
            if total_energy_wh >= 100:
                info_big = f"Energy: {total_energy_wh / 1000:.2f} kWh"
            else:
                info_big = f"Energy: {total_energy_wh:.1f} Wh"
            info_attr = curses.color_pair(C_CYAN) | curses.A_BOLD
            draw_line(pad, info_y, 2, info_big[:max_x - 3], info_attr)

            # Small text below: cost, time, temp
            detail_y = info_y + 1
            cost_str = format_cost(cost, args.currency)
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

            # ---- Footer ----
            footer_y = max_y - 1
            if help_visible:
                footer_text = " p:pause  r:reset  c:cost  h:hide  q:quit  esc:quit"
            else:
                footer_text = " p:pause  r:reset  c:setcost  h:help  q/esc:quit"

            for i in range(max_x):
                safe_addch(pad, footer_y, i, " ", curses.color_pair(C_BLACK_BG) | curses.A_BOLD)
            draw_line(pad, footer_y, 2, footer_text[:max_x - 3],
                     curses.color_pair(C_CYAN) | curses.A_BOLD)

            # Only blit to screen if data actually changed (prevents blinking)
            if needs_redraw:
                pad.noutrefresh(0, 0, 0, 0, max_y - 1, max_x - 1)
                curses.doupdate()
                needs_redraw = False  # consumed this frame

    except KeyboardInterrupt:
        pass
    finally:
        reader.shutdown()
        curses.curs_set(1)


def main():
    parser = argparse.ArgumentParser(description="Terminal power tracker — compact sidebar.")
    parser.add_argument("-i", "--interval", type=int, default=1000, help="Polling interval in ms")
    args = parser.parse_args()

    # Get cost from config (prompts on first run)
    cost_per_kwh = get_cost_per_kwh()
    currency = get_currency()

    class Args:
        interval = args.interval
        cost_per_kwh = cost_per_kwh
        currency = currency
    curses.wrapper(lambda stdscr: curses_main(stdscr, Args()))


if __name__ == "__main__":
    main()
