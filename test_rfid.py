"""
Standalone RDM6300 diagnostic — run this directly, no Flask needed.
  python test_rfid.py
"""
import sys
import time

# ── 1. pyserial check ────────────────────────────────────────────────────────
try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("ERROR: pyserial not installed.\n  pip install pyserial")
    sys.exit(1)

# ── 2. List every available port ─────────────────────────────────────────────
print("=== Available serial ports ===")
ports = list(list_ports.comports())
if not ports:
    print("  (none found)")
else:
    for p in ports:
        vid = hex(p.vid) if p.vid else "n/a"
        print(f"  {p.device:10s}  VID={vid:8s}  {p.description}")

# ── 3. Pick the CP2102 (VID 0x10C4) or ask the user ─────────────────────────
CP210X_VID = 0x10C4
target = next((p.device for p in ports if p.vid == CP210X_VID), None)

if target:
    print(f"\n>>> Auto-selected CP2102 on {target}")
else:
    print("\nNo CP2102 found automatically.")
    target = input("Enter port manually (e.g. COM3 or /dev/ttyUSB0): ").strip()
    if not target:
        sys.exit(1)

# ── 4. Try to open the port ───────────────────────────────────────────────────
print(f"\nOpening {target} at 9600 baud …")
try:
    ser = serial.Serial(target, 9600, timeout=2)
except serial.SerialException as exc:
    print(f"ERROR: Cannot open port: {exc}")
    sys.exit(1)

print("Port open. Hold an RFID card to the reader (Ctrl+C to quit).\n")

# ── 5. Read raw bytes and look for RDM6300 packets ───────────────────────────
STX, ETX = 0x02, 0x03
buf = bytearray()

try:
    while True:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            print(f"  raw bytes: {chunk.hex(' ')}")
            buf.extend(chunk)

        # Try to parse a complete 14-byte packet
        while len(buf) >= 14:
            try:
                start = buf.index(STX)
            except ValueError:
                buf.clear()
                break

            if start > 0:
                del buf[:start]

            if len(buf) < 14:
                break

            packet = bytes(buf[:14])
            del buf[:14]

            if packet[13] != ETX:
                continue

            data = packet[1:11].decode("ascii", errors="replace")
            rx_cs = int(packet[11:13], 16)
            calc_cs = 0
            for i in range(0, 10, 2):
                calc_cs ^= int(data[i:i+2], 16)

            if calc_cs != rx_cs:
                print(f"  BAD checksum (got {rx_cs:02X}, expected {calc_cs:02X})")
                continue

            uid = data[2:]
            print(f"\n  *** CARD DETECTED: UID = {uid} ***\n")

except KeyboardInterrupt:
    pass
finally:
    ser.close()
    print("\nDone.")
