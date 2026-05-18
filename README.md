# ecost — Terminal Energy Tracker

A real-time system power monitor for the terminal — inspired by [nvtop](https://github.com/Syllo/nvtop).

Displays instantaneous power consumption of **GPU(s)**, **CPU** (RAPL), **DRAM**, and **storage** in a curses-based UI with live bar gauges and a history chart.

## Hardware Tested

- **CPU:** AMD Ryzen 9 5950X (RAPL powercap)
- **GPU:** Dual NVIDIA RTX 3090 (NVML)
- **RAM:** DDR4-3200 (estimated)
- **Storage:** NVMe SATA SSD (estimated from I/O)

## Requirements

- Python 3.10+
- NVIDIA GPU with NVML support (optional, GPU panel hides if unavailable)
- Linux (for RAPL `/sys/class/powercap`)
- `curses` (built into Python on Linux)

## Installation

```bash
cd terminal-energy-tracker
pip install . --break-system-packages
```

Then run:

```bash
ecost
```

On first run it will prompt for your electricity cost per kWh.

Or manually (without installing):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Usage

```bash
ecost [-i INTERVAL_MS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `-i, --interval` | 1000 | Polling interval in milliseconds |

Electricity cost is stored in `~/.config/ecost/config.json` and set on first run.

## Keyboard Controls

| Key | Action |
|-----|--------|
| `q` / `Esc` | Quit |
| `p` | Pause |
| `r` | Resume |
| `h` | Toggle help in footer |

## Layout

```
┌──────────────────────────────────────────────────────────┐
│  ◆ Energy Tracker (Terminal) ◆                           │
│  Total:   425.3 W  |  Max:   580.2 W  |  Avg:   380.1 W │
│  Time: 00:12:34  |  Energy:    0.142 kWh  |  Cost: Rs.12 │
├──────────────────────────────────────────────────────────┤
│ ┌──── GPU 0 (RTX 3090) ────┐ ┌─── GPU 1 (RTX 3090) ───┐│
│ │ 185.3 W  |█████████░░░░░ │ │  52.1 W  |███░░░░░░░░░ ││
│ │    0.421 Wh               │ │    0.113 Wh             ││
│ └───────────────────────────┘ └─────────────────────────┘│
│ ┌──── CPU (RAPL) ────────┐ ┌─────── DRAM (64GB) ──────┐│
│ │ 142.7 W  |███████░░░░░ │ │  12.5 W  |██░░░░░░░░░░░ ││
│ │ Temp: 62°C  0.312 Wh   │ │        0.028 Wh           ││
│ └─────────────────────────┘ └──────────────────────────┘│
│ ┌──────── Storage (total) ─────────────────────────────┐│
│ │   4.2 W  |█░░░░░░░░░░░░ │                            ││
│ │         0.009 Wh                               ││
│ └──────────────────────────────────────────────────────┘│
│                                                          │
│  Power History (max: 580.2 W)                            │
│  ▁▂▃▅▆▇█▇▆▅▄▃▂▁▂▃▅▆▇█▇▆▅▄▃▂▁▂▃▅▆▇█▇▆▅▄▃▂▁               │
│                                                          │
│  [p]ause  [r]esume  [h]elp  [q]uit                      │
└──────────────────────────────────────────────────────────┘
```

## RAPL Permissions

CPU power reading requires read access to `/sys/class/powercap/*/energy_uj`.

If you get permission errors, create the udev rule:

```bash
sudo bash -c 'cat > /etc/udev/rules.d/99-energy-tracker-rapl.rules << EOF
SUBSYSTEM=="powercap", KERNEL=="intel-rapl:*", MODE="0644"
SUBSYSTEM=="powercap", KERNEL=="amd-rapl:*", MODE="0644"
EOF'
sudo udevadm control --reload-rules
```

## Notes

- DRAM and Storage power are **estimates** based on usage heuristics
- CPU uses RAPL `energy_uj` counter (accurate) with k10temp fallback
- GPU uses NVML `nvmlDeviceGetPowerUsage()` (accurate, milliwatt precision)
- Cost is calculated as `kWh × cost_per_kwh` (set on first run, stored in `~/.config/ecost/config.json`)
