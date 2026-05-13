"""
core/balance_board.py
Wii Balance Board — conexão via HID nativo do Windows + simulador.

Estratégia de conexão (sem PyBluez, compatível com Python 3.14):
  1. O usuário PAREIA a Balance Board pelo Bluetooth do Windows normalmente
  2. O Windows expõe o dispositivo como HID (Human Interface Device)
  3. Usamos 'hid' (python-hid) para abrir o dispositivo e ler pacotes brutos

Instalação:
    pip install hid

Se 'hid' também não funcionar:
    pip install hidapi

Identificação do dispositivo:
    vendor_id  = 0x057E  (Nintendo)
    product_id = 0x0306  (Balance Board)

Protocolo:
  - Relatório 0x32 (extensão): bytes 4-11 contêm os 4 sensores × 2 bytes
  - Calibração em memória 0xA40024 via relatório de leitura 0x21
  - Ordem dos sensores: TR, BR, TL, BL (top/bottom right/left)
"""

import struct
import time
import math
import random
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable


# IDs do hardware Nintendo Balance Board
NINTENDO_VID  = 0x057E
BALANCE_PID   = 0x0306


@dataclass
class SensorData:
    top_left:     float
    top_right:    float
    bottom_left:  float
    bottom_right: float
    timestamp: float = field(default_factory=time.time)

    def total(self) -> float:
        return self.top_left + self.top_right + self.bottom_left + self.bottom_right


class CalibrationTable:
    """
    3 pontos de calibração por sensor: 0 kg, 17 kg, 34 kg.
    Interpolação linear entre os pontos para converter raw → kg.
    """
    SENSORS = ["top_right", "bottom_right", "top_left", "bottom_left"]

    def __init__(self, raw_bytes: Optional[bytes] = None):
        self.points = {s: [0, 1700, 3400] for s in self.SENSORS}
        if raw_bytes and len(raw_bytes) >= 24:
            self._parse(raw_bytes)

    def _parse(self, data: bytes):
        for i, sensor in enumerate(self.SENSORS):
            self.points[sensor] = [
                struct.unpack_from(">H", data, i * 2)[0],
                struct.unpack_from(">H", data, 8  + i * 2)[0],
                struct.unpack_from(">H", data, 16 + i * 2)[0],
            ]

    def raw_to_kg(self, sensor: str, raw: int) -> float:
        cal = self.points.get(sensor, [0, 1700, 3400])
        if raw < cal[1]:
            low, high, ref_low, ref_high = cal[0], cal[1], 0.0, 17.0
        else:
            low, high, ref_low, ref_high = cal[1], cal[2], 17.0, 34.0
        span = high - low
        if span == 0:
            return ref_low
        return ref_low + (raw - low) / span * (ref_high - ref_low)


class BalanceBoardHardware:
    """
    Conexão com a Wii Balance Board via HID nativo do Windows.
    Não requer PyBluez — usa apenas a biblioteca 'hid' (python-hid).

    Pré-requisitos:
      1. pip install hid
      2. Parear a Balance Board pelo Bluetooth do Windows:
         Configurações → Bluetooth → Adicionar dispositivo → Nintendo RVL-WBC-01
      3. Clicar em "Conectar Balance Board" no dashboard

    Fluxo interno:
      connect() → abre o dispositivo HID → envia init → lê calibração
               → habilita streaming → inicia thread de leitura
    """

    HID_SET_REPORT = 0x52
    RPT_WRITE_MEM  = 0x16
    RPT_READ_MEM   = 0x17
    RPT_DATA_MODE  = 0x12

    REG_EXT_INIT1   = 0xA400F0
    REG_EXT_INIT2   = 0xA400FB
    REG_CALIBRATION = 0xA40024

    def __init__(self,
                 on_data:   Optional[Callable[[SensorData], None]] = None,
                 on_status: Optional[Callable[[str], None]] = None):
        self.on_data   = on_data
        self.on_status = on_status
        self.calibration = CalibrationTable()
        self._dev = None
        self._connected = False
        self._running   = False
        self._thread: Optional[threading.Thread] = None
        self._pending_cal: Optional[bytes] = None
        self._cal_event = threading.Event()

    def _log(self, msg: str):
        print(f"[hardware] {msg}")
        if self.on_status:
            self.on_status(msg)

    def _find_device(self):
        """Procura a Balance Board na lista de dispositivos HID."""
        try:
            import hid
        except ImportError:
            raise RuntimeError(
                "Biblioteca 'hid' não instalada.\n"
                "Execute: pip install hid"
            )
        devices = hid.enumerate(NINTENDO_VID, BALANCE_PID)
        if not devices:
            # Tenta sem filtro e mostra o que encontrou (ajuda no diagnóstico)
            all_devs = hid.enumerate()
            nintendo = [d for d in all_devs if d["vendor_id"] == NINTENDO_VID]
            if nintendo:
                self._log(f"Nintendo encontrado mas produto diferente: {nintendo[0]}")
            return None
        return devices[0]["path"]

    def connect(self) -> bool:
        try:
            import hid
        except ImportError:
            self._log("Instale 'hid': pip install hid")
            return False

        self._log("Procurando Balance Board nos dispositivos HID...")
        path = self._find_device()
        if not path:
            self._log(
                "Balance Board não encontrada.\n"
                "Certifique que ela está PAREADA (não só conectada) no Bluetooth do Windows.\n"
                "Configurações → Bluetooth → Adicionar dispositivo → Nintendo RVL-WBC-01"
            )
            return False

        try:
            self._dev = hid.device()
            self._dev.open_path(path)
            self._dev.set_nonblocking(False)
            self._log(f"Dispositivo aberto: {self._dev.get_manufacturer_string()} "
                      f"{self._dev.get_product_string()}")
        except Exception as e:
            self._log(f"Erro ao abrir dispositivo HID: {e}")
            return False

        # Inicializa extensão
        self._write_reg(self.REG_EXT_INIT1, b"\x55")
        time.sleep(0.1)
        self._write_reg(self.REG_EXT_INIT2, b"\x00")
        time.sleep(0.1)

        # LED 1 aceso (sinaliza conexão ao usuário)
        self._send([self.HID_SET_REPORT, 0x11, 0x10])
        time.sleep(0.05)

        # Lê calibração
        self._log("Lendo calibração dos sensores...")
        self._request_calibration()
        self._cal_event.wait(timeout=3.0)
        if self._pending_cal:
            self.calibration = CalibrationTable(self._pending_cal)
            self._log("Calibração carregada.")
        else:
            self._log("Calibração não lida; usando valores padrão.")

        # Habilita streaming do relatório de extensão 0x32
        self._send([self.HID_SET_REPORT, self.RPT_DATA_MODE, 0x04, 0x32])
        time.sleep(0.05)

        self._connected = True
        self._running   = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._log("Conectado! Streaming de dados iniciado.")
        return True

    def _send(self, data: list):
        if self._dev:
            try:
                # HID report: prefixo 0x00 (report id) no Windows
                self._dev.write([0x00] + data)
            except Exception as e:
                self._log(f"Erro ao enviar: {e}")

    def _write_reg(self, address: int, data: bytes):
        addr_bytes = list(struct.pack(">I", address)[1:])  # 3 bytes
        payload = [self.HID_SET_REPORT, self.RPT_WRITE_MEM, 0x04] \
                  + addr_bytes + [len(data) - 1] + list(data.ljust(16, b"\x00"))
        self._send(payload)

    def _request_calibration(self):
        addr_bytes = list(struct.pack(">I", self.REG_CALIBRATION)[1:])
        size_bytes = list(struct.pack(">H", 24))
        self._send([self.HID_SET_REPORT, self.RPT_READ_MEM, 0x04]
                   + addr_bytes + size_bytes)

    def _read_loop(self):
        self._dev.set_nonblocking(False)
        while self._running:
            try:
                packet = self._dev.read(64, timeout_ms=500)
            except Exception:
                if self._running:
                    time.sleep(0.01)
                continue

            if not packet or len(packet) < 2:
                continue

            report_id = packet[1]

            if report_id == 0x21:   # resposta de leitura (calibração)
                self._handle_read_data(packet)
            elif report_id == 0x32: # extensão — sensores
                sd = self._parse_sensors(packet)
                if sd and self.on_data:
                    self.on_data(sd)
            elif report_id == 0x34: # core + extensão (alguns firmwares)
                sd = self._parse_sensors_0x34(packet)
                if sd and self.on_data:
                    self.on_data(sd)

    def _handle_read_data(self, packet):
        if len(packet) < 7:
            return
        error = packet[3] & 0x0F
        if error:
            self._cal_event.set()
            return
        size = ((packet[3] >> 4) & 0x0F) + 1
        data = bytes(packet[7:7 + size])
        if self._pending_cal is None:
            self._pending_cal = b""
        self._pending_cal += data
        if len(self._pending_cal) >= 24:
            self._cal_event.set()

    def _parse_sensors(self, packet) -> Optional[SensorData]:
        if len(packet) < 12:
            return None
        try:
            raw_tr, raw_br, raw_tl, raw_bl = struct.unpack_from(">HHHH", bytes(packet), 4)
        except struct.error:
            return None
        return SensorData(
            top_left     = self.calibration.raw_to_kg("top_left",     raw_tl),
            top_right    = self.calibration.raw_to_kg("top_right",    raw_tr),
            bottom_left  = self.calibration.raw_to_kg("bottom_left",  raw_bl),
            bottom_right = self.calibration.raw_to_kg("bottom_right", raw_br),
        )

    def _parse_sensors_0x34(self, packet) -> Optional[SensorData]:
        if len(packet) < 14:
            return None
        try:
            raw_tr, raw_br, raw_tl, raw_bl = struct.unpack_from(">HHHH", bytes(packet), 6)
        except struct.error:
            return None
        return SensorData(
            top_left     = self.calibration.raw_to_kg("top_left",     raw_tl),
            top_right    = self.calibration.raw_to_kg("top_right",    raw_tr),
            bottom_left  = self.calibration.raw_to_kg("bottom_left",  raw_bl),
            bottom_right = self.calibration.raw_to_kg("bottom_right", raw_br),
        )

    def disconnect(self):
        self._running   = False
        self._connected = False
        try:
            self._send([self.HID_SET_REPORT, 0x11, 0x00])  # apaga LEDs
        except Exception:
            pass
        if self._dev:
            try:
                self._dev.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
        self._log("Desconectado.")

    def read(self) -> Optional[SensorData]:
        return None  # usa callback on_data, não polling


# ── Simulador ────────────────────────────────────────────────────────────────

class BalanceBoardSimulator:
    """Interface idêntica ao hardware real — usa callback on_data."""

    def __init__(self, exercise: str = "squat",
                 on_data:   Optional[Callable[[SensorData], None]] = None,
                 on_status: Optional[Callable[[str], None]] = None,
                 rate_hz: float = 10.0):
        self.exercise  = exercise
        self.on_data   = on_data
        self.on_status = on_status
        self._rate     = rate_hz
        self._tick     = 0
        self._running  = False
        self._thread: Optional[threading.Thread] = None

    def connect(self) -> bool:
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if self.on_status:
            self.on_status(f"Simulador iniciado ({self.exercise})")
        return True

    def _loop(self):
        interval = 1.0 / self._rate
        while self._running:
            time.sleep(interval)
            self._tick += 1
            tl, tr, bl, br = PATTERNS.get(self.exercise, _stand_pattern)(self._tick)
            sd = SensorData(
                top_left     = max(0.0, tl),
                top_right    = max(0.0, tr),
                bottom_left  = max(0.0, bl),
                bottom_right = max(0.0, br),
            )
            if self.on_data:
                self.on_data(sd)

    def read(self) -> Optional[SensorData]:
        return None

    def disconnect(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)


# ── Padrões de simulação ─────────────────────────────────────────────────────

def _squat_pattern(t):
    n = lambda s=1.5: random.gauss(0, s)
    return (18 + math.sin(t*0.08)*6 + n(),
            17 + math.sin(t*0.08+0.3)*6 + n(),
            15 + math.cos(t*0.08)*4 + n(),
            20 + math.sin(t*0.05)*5 + n())

def _balance_pattern(t):
    n = lambda s=2.5: random.gauss(0, s)
    return (5  + math.sin(t*0.12)*4 + n(),
            30 + math.sin(t*0.09)*8 + n(),
            4  + math.cos(t*0.11)*3 + n(),
            31 + math.cos(t*0.07)*7 + n())

def _stand_pattern(t):
    n = lambda: random.gauss(0, 0.8)
    return 17+n(), 18+n(), 17+n(), 18+n()

def _lunge_pattern(t):
    n = lambda s=2.0: random.gauss(0, s)
    return (25 + math.sin(t*0.06)*10 + n(),
            10 + math.sin(t*0.06+math.pi)*5 + n(),
            15 + math.cos(t*0.05)*8 + n(),
            8  + math.cos(t*0.05+math.pi)*4 + n())

PATTERNS = {
    "squat":   _squat_pattern,
    "balance": _balance_pattern,
    "stand":   _stand_pattern,
    "lunge":   _lunge_pattern,
}
