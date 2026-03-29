# autonom-drinks

Self-service bar payment system for the sailing club. Members select drinks on a touchscreen, then hold their RFID chip to the reader to pay. Everything runs on a Raspberry Pi — no internet required for daily operation.

## Features

- Touch-optimised kiosk UI for drink selection
- RFID payment (RC522 reader, 13.56 MHz Mifare cards/fobs)
- Member management via CSV import
- Balance top-up in the admin panel
- Transaction history and revenue overview
- Periodic offsite data sync (pluggable provider — Supabase, PostgreSQL, etc.)

---

## Hardware

### Required

| Part | Notes |
|------|-------|
| Raspberry Pi 3B+ / 4 / 5 | Pi 4 or 5 recommended for snappier UI |
| Touchscreen display | Official RPi 7" DSI display works out of the box; any HDMI touch panel also works |
| RDM6300 RFID module | ~€5 on Amazon/AliExpress, 125 kHz UART |
| RFID cards or key fobs | EM4100/EM4102 compatible, 125 kHz, one per member |
| MicroSD card | 16 GB+ Class 10 / A1 |
| Power supply | Official RPi PSU (5V 3A for Pi 4/5) |
| Jumper wires (female–female) | For wiring the RDM6300 to the GPIO header |

### RDM6300 → Raspberry Pi wiring

The RDM6300 communicates over UART at 9600 8N1. Only TX → RX is needed (the module sends data; no commands go back to it).

```
RDM6300 pin   →   RPi GPIO (BCM)   →   Physical pin
-----------       ---------------       -------------
TX            →   GPIO 15 (RXD)    →   Pin 10
RX            →   (not connected)
VCC           →   5V               →   Pin 2
GND           →   GND              →   Pin 6
```

> **Note:** The RDM6300 is 5 V powered but its TX output is 3.3 V compatible — safe to connect directly to the Pi's RX pin.

### Touchscreen notes

- **Official RPi 7" DSI display**: connects via the DSI ribbon cable, no extra config needed on Pi 4/5. On Pi 3 add `display_rotate=0` to `/boot/config.txt` if the image is upside down.
- **HDMI touchscreen**: plug in and enable touch via `dtoverlay=ads7846` or the manufacturer's overlay.

---

## Software installation

### 1 — Flash Raspberry Pi OS

Use **Raspberry Pi Imager** to flash **Raspberry Pi OS Lite (64-bit)** or the full desktop version.
In the imager settings set hostname, SSH, Wi-Fi, and locale before flashing — saves time.

### 2 — Enable UART (for the RFID reader)

```bash
sudo raspi-config
# → Interface Options → Serial Port
# "Would you like a login shell over serial?" → No
# "Would you like the serial port hardware to be enabled?" → Yes
sudo reboot
```

Verify after reboot:
```bash
ls /dev/ttyS0
# should show /dev/ttyS0
```

> On Pi 3 the hardware UART is `/dev/ttyAMA0` by default (used by Bluetooth). If `/dev/ttyS0` is unreliable, disable Bluetooth (`dtoverlay=disable-bt` in `/boot/config.txt`) and change `UART_PORT` in [app/services/rfid.py](app/services/rfid.py) to `/dev/ttyAMA0`.

### 3 — System dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git
```

### 4 — Clone and install

```bash
git clone https://github.com/YOUR_ORG/autonom-drinks.git
cd autonom-drinks

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install mfrc522          # RFID library (RPi only)
```

### 5 — Configure environment

```bash
cp .env.example .env
nano .env
```

Minimum settings to change:

```env
FLASK_ENV=production
SECRET_KEY=<random string, e.g. output of: python3 -c "import secrets; print(secrets.token_hex(32))">
ADMIN_PASSWORD=<your admin password>
RFID_MOCK=false
```

### 6 — Run once to verify

```bash
source .venv/bin/activate
python run.py
```

Open `http://localhost:5000` in a browser. If you see the kiosk UI the install is working.

---

## Kiosk mode (auto-start on boot)

### Install Chromium kiosk service

```bash
sudo apt install -y chromium-browser unclutter
```

Create the service file:

```bash
sudo nano /etc/systemd/system/autonom-drinks.service
```

```ini
[Unit]
Description=autonom-drinks Flask server
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/autonom-drinks
EnvironmentFile=/home/pi/autonom-drinks/.env
ExecStart=/home/pi/autonom-drinks/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create the kiosk autostart (desktop session):

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/kiosk.desktop
```

```ini
[Desktop Entry]
Type=Application
Name=Bar Kiosk
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://localhost:5000
```

Enable and start the Flask service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable autonom-drinks
sudo systemctl start autonom-drinks
```

Reboot and the kiosk will start automatically, browser in fullscreen.

### Hide the mouse cursor (touchscreen only)

Add to `~/.config/autostart/unclutter.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Unclutter
Exec=unclutter -idle 0.1 -root
```

---

## Admin panel

Accessible from any device on the same network:

```
http://<pi-ip-address>:5000/admin
```

Default password is set in `.env` → `ADMIN_PASSWORD`.

| Section | What you can do |
|---------|----------------|
| Dashboard | Revenue totals, member count, trigger manual sync |
| Drinks | Add / edit / remove drinks, set price, toggle availability, reorder |
| Members | Import via CSV, top up balances, deactivate members |
| Analytics | Full transaction log |

### Member CSV format

```csv
rfid,lastname,firstname
1234567890,Müller,Hans
9876543210,Schmidt,Anna
```

Column order does not matter. Rows with a duplicate RFID are skipped automatically.

---

## Offsite sync

By default sync is disabled (`SYNC_PROVIDER=none`). The `synced` flag on each transaction tracks what has not yet been pushed. To add a provider:

1. Implement `BaseSyncProvider` in `app/services/sync.py`
2. Register it in the `_build_provider` function
3. Set `SYNC_PROVIDER=yourprovider` and `SYNC_DSN=...` in `.env`

The sync job runs every `SYNC_INTERVAL_SECONDS` (default 300 s) and can also be triggered manually from the admin dashboard.

---

## Development (without a Pi)

```bash
cp .env.example .env
# RFID_MOCK=true is already the default for development

source .venv/bin/activate
python run.py
```

Simulate a card tap via the API:

```bash
curl -X POST http://localhost:5000/api/rfid/inject \
     -H "Content-Type: application/json" \
     -d '{"uid": "1234567890"}'
```

---

## Pi model notes

| Model | Notes |
|-------|-------|
| Pi 3B+ | Supported. Flask + SQLite is fine; expect ~2 s cold start. Use 32-bit OS. |
| Pi 4 (2 GB+) | Recommended. Fast enough that the UI feels instant. 64-bit OS. |
| Pi 5 | Works great. No special config needed. Active cooler recommended if in an enclosure. |
