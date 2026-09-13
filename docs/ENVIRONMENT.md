# Verified platform

The exact environment this gateway was built and run against. Recorded from the board
itself rather than from documentation, because "should be" and "is" diverge on developer
hardware and a reader trying to reproduce this needs the second one.

Captured 21 August 2026.

## Board

| | |
|---|---|
| Hardware | Arduino UNO Q, `ABX00162` (4 GB) |
| Host SoC | Qualcomm QRB2210, quad Cortex-A53, `aarch64` |
| MCU | STM32U585, Cortex-M33 |
| Hostname | `sentinel-q` |

## Host Linux side

```
$ uname -a
Linux sentinel-q 6.16.7-g0dd6551ae96b #1 SMP PREEMPT Tue Sep 23 12:46:06 UTC 2025 aarch64 GNU/Linux

$ cat /etc/os-release
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
VERSION_ID="13"
DEBIAN_VERSION_FULL=13.1
```

Storage at first use: 7 GB of 27 GB consumed. RAM 3.58 GB total.

## Arduino toolchain

```
$ arduino-cli core list
ID             Installed Latest Name
arduino:zephyr 0.90.0    0.90.0 Arduino UNO Q Board
```

Also installed by the App Lab board update: `adbd` 34.0.5-12arduino7, `arduino-app-cli`
0.13.0, `arduino-app-lab` 0.10.0, `arduino-cli` 1.5.1, `arduino-router` 0.10.0.

**Why the core version is called out.** Native FDCAN1 on D4 (TX) / D5 (RX) landed in
ArduinoCore-zephyr 0.55.0. At 0.90.0 it is available to us without touching the loader —
which is the one operation that bricks these boards. The CAN work in this project is
possible because the shipped core was already new enough, not because anything risky was
done to get there.

## Bluetooth

```
$ bluetoothctl --version
bluetoothctl: 5.82

$ systemctl is-active bluetooth
active

$ bluetoothctl list
Controller 14:B5:CD:EB:32:85 uno-q [default]
```

BlueZ is present and running **on the host Debian side**. This is the finding the whole
BLE architecture rests on: App Lab applications run in a container with no D-Bus and no
BlueZ, so a BLE central cannot live there. It lives on the host and is reached from the
container over HTTP. See the README for why that seam exists.

Receiver sensitivity observed in an ordinary indoor scan: advertisements resolved down to
−93 dBm, against a −70 dBm working threshold at bench distance. Ample margin.

## The POWER button (JBTN1)

Observed behaviour, because the documentation does not spell it out and the answer
matters twice over — once for shutting the board down safely, and once for the driver
procedure this project is built around.

| Action | Result |
|---|---|
| Short press (~1 s) | Nothing. No `KEY_POWER` event reaches systemd-logind, so no clean shutdown. |
| Long press, USB power applied | PMIC drops `PS_HOLD` — a hard reset. The board goes dark and immediately reboots, because the power source is still present. |
| Long press, then unplug while dark | The only reliable way to leave it powered off. |

JBTN1 is wired to the Qualcomm PM4125 PMIC on `KYPDPWR_N`. It is not a GPIO and a sketch
cannot read it. **This is why the driver's pre-trip inspection button is an external
momentary switch on D2** (`INPUT_PULLUP`, pressed = LOW) rather than the button already on
the board — an early assumption that had to be abandoned once the schematic was traced.

Without root there is no `poweroff`, so before removing power:

```bash
sync; sync
```

On an idle board with a journaling filesystem that reduces the risk of an unclean
unplug to negligible.

## Python

Debian 13 enforces PEP 668, so the system interpreter refuses `pip install`. It also
ships **without `pip` and without `ensurepip`**, so `python3 -m venv` fails out of the box
with a message pointing at a `python3.13-venv` package.

What worked, without root:

```bash
curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
python3 /tmp/get-pip.py --user --break-system-packages
python3 -m pip install --user --break-system-packages bleak
```

Installed: `bleak` 3.0.2, `dbus-fast` 5.0.22 — both as prebuilt `aarch64` wheels, so
nothing compiles on the board.

`~/.local/bin` is not on `PATH` by default; invoking through `python3 -m pip` sidesteps
that rather than editing shell configuration.

## A note on reproducing this

Every version above is pinned in `requirements.txt` where it is a Python dependency, and
stated here where it is not. If you are reading this because something behaves
differently on your board, the core version and the Debian release are the two most
likely places for the difference to be hiding.
