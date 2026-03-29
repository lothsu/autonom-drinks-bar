"""
RFID service for 125 kHz readers (RDM6300 and compatible).

The RDM6300 speaks UART at 9600 8N1 and sends 14 bytes each time a card
is presented:
    0x02  STX
    [10 bytes]  ASCII hex — version (2) + card ID (8)
    [2 bytes]   ASCII hex checksum (XOR of the five 2-char pairs)
    0x03  ETX

We only keep the 8-char card ID (the last 4 bytes of the data section).

Port selection (RFID_PORT env var):
  "auto"          — try CP2102 USB bridge first, then fall back to GPIO UART
  "/dev/ttyUSB0"  — explicit Linux/RPi USB serial (CP2102 via bridge)
  "/dev/ttyS0"    — explicit RPi GPIO UART (direct wiring)
  "COM3"          — explicit Windows COM port
"""

import threading
import time
from datetime import datetime, timezone

UART_PORT_GPIO = "/dev/ttyS0"   # RPi GPIO UART (direct wiring)
UART_BAUD      = 9600
PACKET_LEN     = 14
STX            = 0x02
ETX            = 0x03

# Silicon Labs CP210x USB-to-UART bridge (VID used by CP2102)
_CP210X_VID = 0x10C4


def _find_port(requested: str) -> str:
    """Resolve the serial port to open.

    'auto' → scan for a CP210x USB bridge; fall back to GPIO UART.
    Anything else is returned as-is.
    """
    if requested != "auto":
        return requested

    try:
        from serial.tools import list_ports  # type: ignore[import]
        for port in list_ports.comports():
            if port.vid == _CP210X_VID:
                print(f"[RFID] Found CP2102 USB bridge on {port.device}")
                return port.device
    except Exception:
        pass

    return UART_PORT_GPIO


def _parse_packet(buf: bytes) -> str | None:
    """Return the 8-hex-char card UID from a valid 14-byte packet, or None."""
    if len(buf) != PACKET_LEN or buf[0] != STX or buf[13] != ETX:
        return None
    data = buf[1:11].decode("ascii", errors="replace")
    checksum_rx = int(buf[11:13], 16)
    checksum_calc = 0
    for i in range(0, 10, 2):
        checksum_calc ^= int(data[i:i+2], 16)
    if checksum_calc != checksum_rx:
        return None
    return data[2:]  # skip 2-char version byte, return 8-char card ID


class RFIDService:
    def __init__(self, mock: bool = False, port: str = "auto"):
        self.mock = mock
        self._port = port
        self._last_uid: str | None = None
        self._last_read_at: datetime | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        self._running = True
        target = self._mock_loop if self.mock else self._hardware_loop
        self._thread = threading.Thread(target=target, daemon=True, name="rfid-reader")
        self._thread.start()

    def stop(self):
        self._running = False

    def get_last_scan(self) -> tuple[str | None, datetime | None]:
        with self._lock:
            return self._last_uid, self._last_read_at

    def clear(self):
        with self._lock:
            self._last_uid = None
            self._last_read_at = None

    def inject_uid(self, uid: str):
        """Inject a UID manually — used in mock/dev mode and for enrolling new cards."""
        with self._lock:
            self._last_uid = uid
            self._last_read_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    def _mock_loop(self):
        while self._running:
            time.sleep(1)

    def _hardware_loop(self):
        try:
            import serial  # type: ignore[import]
        except ImportError:
            print("[RFID] pyserial not installed — run: pip install pyserial")
            return

        port = _find_port(self._port)
        try:
            ser = serial.Serial(port, UART_BAUD, timeout=1)
            print(f"[RFID] Opened {port} at {UART_BAUD} baud")
        except Exception as exc:
            print(f"[RFID] Cannot open {port}: {exc}")
            return

        buf = bytearray()
        candidate_uid: str | None = None
        candidate_count: int = 0
        CONFIRM_READS = 2  # number of matching reads before accepting a UID

        while self._running:
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except Exception as exc:
                print(f"[RFID] Read error: {exc}")
                time.sleep(1)
                continue

            buf.extend(chunk)

            # Scan buffer for complete packets
            while len(buf) >= PACKET_LEN:
                try:
                    start = buf.index(STX)
                except ValueError:
                    buf.clear()
                    break

                if start > 0:
                    del buf[:start]  # discard garbage before STX

                if len(buf) < PACKET_LEN:
                    break

                packet = bytes(buf[:PACKET_LEN])
                del buf[:PACKET_LEN]

                uid = _parse_packet(packet)
                if not uid:
                    candidate_uid = None
                    candidate_count = 0
                    continue

                if uid == candidate_uid:
                    candidate_count += 1
                else:
                    candidate_uid = uid
                    candidate_count = 1

                if candidate_count >= CONFIRM_READS:
                    with self._lock:
                        self._last_uid = uid
                        self._last_read_at = datetime.now(timezone.utc)
                    print(f"[RFID] Confirmed UID: {uid}")

        ser.close()
