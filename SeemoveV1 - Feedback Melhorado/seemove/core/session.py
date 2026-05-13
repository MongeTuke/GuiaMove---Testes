"""
core/session.py
Loop principal com máquina de estados de feedback instrucional.

Estados do feedback:
  IDLE      → sem desvio, monitorando silenciosamente
  INSTRUCTING → desvio detectado, instrução emitida, aguardando correção
  WAITING   → instrução dada, em silêncio enquanto o usuário ajusta (janela de 5s)
  CONFIRMING → usuário corrigiu, emite confirmação positiva uma vez
  REINFORCING → não corrigiu após a janela, reforça com dica adicional

Transições:
  IDLE        + desvio          → INSTRUCTING
  INSTRUCTING + (automático)    → WAITING
  WAITING     + corrigiu        → CONFIRMING
  WAITING     + não corrigiu    → REINFORCING (nova instrução ou reforço)
  CONFIRMING  + (automático)    → IDLE
  REINFORCING + (automático)    → WAITING
"""

import time
import threading
from enum import Enum
from core.balance_board import SensorData
from core.cog import calculate_cog, CoGHistory, CoGStats
from audio.tts_engine import TTSEngine
from audio.sonification import SonificationEngine
from exercises.base import Exercise, DeviationType
from reports.reporter import SessionReporter
from config.settings import Settings


class FeedbackState(Enum):
    IDLE        = "idle"
    INSTRUCTING = "instructing"
    WAITING     = "waiting"
    CONFIRMING  = "confirming"
    REINFORCING = "reinforcing"


# Frases de confirmação — variadas para não ficar repetitivo
CONFIRMATIONS = [
    "Isso, muito bom.",
    "Perfeito, assim está certo.",
    "Ótimo, continue assim.",
    "Muito bem.",
]

# Reforços quando o usuário não corrigiu na janela de espera
REINFORCEMENTS = {
    DeviationType.LEFT:     "Ainda há peso excessivo à esquerda. Tente rolar o peso lentamente para o centro.",
    DeviationType.RIGHT:    "Ainda há peso excessivo à direita. Tente rolar o peso lentamente para o centro.",
    DeviationType.FORWARD:  "Você ainda está inclinado para frente. Puxe levemente o abdômen para dentro.",
    DeviationType.BACKWARD: "Você ainda está com peso nos calcanhares. Tente dobrar levemente os joelhos.",
    DeviationType.INSTABLE: "Continue tentando. Respire fundo e contraia levemente o abdômen.",
    DeviationType.CRITICAL: "Apoie as duas pernas agora.",
    DeviationType.NO_WEIGHT:"Aguardando você subir na plataforma.",
    DeviationType.NONE:     "",
}


class Session:
    WAIT_WINDOW_S    = 5.0   # segundos de silêncio após instrução
    CONFIRM_FRAMES   = 8     # frames consecutivos OK para confirmar
    REINFORCE_MAX    = 2     # máximo de reforços antes de dar nova pausa longa
    CORRECTION_PAUSE = 12.0  # pausa longa após ciclo de reforços

    def __init__(self, board, tts, sonification, exercise, settings, reporter,
                 web_push=None, web_tts=None, web_status=None):
        self.board        = board
        self.tts          = tts
        self.sonification = sonification
        self.exercise     = exercise
        self.settings     = settings
        self.reporter     = reporter
        self.web_push     = web_push
        self.web_tts      = web_tts
        self.web_status   = web_status

        self.history = CoGHistory(max_size=300)
        self.stats   = CoGStats()

        # Máquina de estados
        self._state          = FeedbackState.IDLE
        self._state_since    = time.time()
        self._last_deviation = DeviationType.NONE
        self._ok_frames      = 0          # frames consecutivos sem desvio
        self._reinforce_count = 0         # quantas vezes reforçou nesse ciclo
        self._confirm_index  = 0          # rotação das frases de confirmação

        self._session_start  = time.time()
        self._lock           = threading.Lock()
        self._stop_event     = threading.Event()

    # ── Callback de dados ───────────────────────────────────────────────────

    def _on_data(self, data: SensorData):
        with self._lock:
            cog = calculate_cog(data, threshold=self.settings.threshold)
            if cog is None:
                return

            self.history.push(cog)
            self.stats.update(cog)
            self.reporter.record(data, cog)

            smoothed = self.history.smoothed(window=self.settings.smoothing_window)
            sx, sy   = smoothed if smoothed else (cog.x, cog.y)

            result   = self.exercise.analyze(sx, sy, cog.total_kg)
            summary  = self.reporter.summary()

            if self.web_push:
                try:
                    self.web_push(data, cog, result, summary)
                except Exception:
                    pass

            self._tick_state_machine(result)
            self._log_terminal(cog, result)

    # ── Máquina de estados ──────────────────────────────────────────────────

    def _tick_state_machine(self, result):
        now      = result.deviation == DeviationType.NONE  # True = postura correta
        elapsed  = time.time() - self._state_since

        if self._state == FeedbackState.IDLE:
            if not now:
                # Novo desvio detectado → instrui imediatamente
                self._set_state(FeedbackState.INSTRUCTING)
                self._last_deviation = result.deviation
                self._reinforce_count = 0
                self._ok_frames = 0
                self._emit(result.message, result.severity)

        elif self._state == FeedbackState.INSTRUCTING:
            # Transição automática para WAITING logo após instrução
            self._set_state(FeedbackState.WAITING)

        elif self._state == FeedbackState.WAITING:
            if now:
                self._ok_frames += 1
                if self._ok_frames >= self.CONFIRM_FRAMES:
                    # Corrigiu! Confirma e volta ao IDLE
                    self._set_state(FeedbackState.CONFIRMING)
                    msg = CONFIRMATIONS[self._confirm_index % len(CONFIRMATIONS)]
                    self._confirm_index += 1
                    self._emit(msg, "ok")
            else:
                self._ok_frames = 0
                if elapsed >= self.WAIT_WINDOW_S:
                    # Janela esgotada sem correção → reforça
                    self._set_state(FeedbackState.REINFORCING)
                    self._reinforce_count += 1

                    if self._reinforce_count <= self.REINFORCE_MAX:
                        # Reforço com dica adicional (ou nova instrução se desvio mudou)
                        if result.deviation != self._last_deviation:
                            # Desvio mudou — nova instrução
                            self._last_deviation = result.deviation
                            self._emit(result.message, result.severity)
                        else:
                            reinf = REINFORCEMENTS.get(self._last_deviation, result.message)
                            self._emit(reinf, result.severity)
                    else:
                        # Muitos reforços — pausa longa para não frustrar
                        self._emit(
                            "Tudo bem, descanse um momento e tente novamente.",
                            "warn"
                        )
                        self._reinforce_count = 0

        elif self._state == FeedbackState.CONFIRMING:
            # Volta ao IDLE após confirmação
            self._set_state(FeedbackState.IDLE)
            self._ok_frames = 0

        elif self._state == FeedbackState.REINFORCING:
            # Volta a WAITING para dar nova janela
            self._set_state(FeedbackState.WAITING)
            self._ok_frames = 0

    def _set_state(self, new_state: FeedbackState):
        self._state       = new_state
        self._state_since = time.time()

    def _emit(self, message: str, severity: str):
        if not message:
            return
        if self.settings.tts_enabled:
            self.tts.speak(message)
        if self.settings.sonification_enabled and severity != "ok":
            self.sonification.play(
                self._last_deviation in (DeviationType.LEFT, DeviationType.RIGHT),
                0.0,
            )
        if self.web_tts:
            try:
                self.web_tts(message, severity)
            except Exception:
                pass

    # ── Terminal log ────────────────────────────────────────────────────────

    def _log_terminal(self, cog, result):
        elapsed = int(time.time() - self._session_start)
        m, s    = divmod(elapsed, 60)
        status  = f"{self._state.value:<12}"
        dev     = result.deviation.value if result.deviation != DeviationType.NONE else "-"
        print(f"  {m:02d}:{s:02d}   {cog.x:+.3f}  {cog.y:+.3f}  "
              f"{cog.total_kg:6.1f}kg  {status}  {dev}")

    # ── Run ─────────────────────────────────────────────────────────────────

    def run(self):
        self.board.on_data = self._on_data
        if self.web_status and hasattr(self.board, 'on_status'):
            self.board.on_status = self.web_status

        print(f"\n  {'TEMPO':>5}  {'X':>7}  {'Y':>7}  {'TOTAL':>8}  {'ESTADO':<14}  DESVIO")
        print("  " + "-" * 60)

        if hasattr(self.board, '_thread'):
            self._stop_event.wait()
        else:
            while not self._stop_event.is_set():
                data = self.board.read()
                if data:
                    self._on_data(data)

    def stop(self):
        self._stop_event.set()
