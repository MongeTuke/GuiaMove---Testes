"""
exercises/base.py
Estruturas base do sistema de feedback instrucional.
"""

from dataclasses import dataclass
from abc import ABC, abstractmethod
from enum import Enum


class DeviationType(Enum):
    """Tipo de desvio detectado — usado pela máquina de estados."""
    NONE      = "none"
    LEFT      = "left"
    RIGHT     = "right"
    FORWARD   = "forward"
    BACKWARD  = "backward"
    INSTABLE  = "instable"
    CRITICAL  = "critical"
    NO_WEIGHT = "no_weight"


@dataclass
class FeedbackResult:
    """
    Resultado da análise biomecânica de um frame.

    message      : texto que será falado (ou exibido no dashboard)
    should_speak : se True, a sessão considera emitir áudio
    severity     : 'ok' | 'warn' | 'error'
    cog_x        : desvio lateral no momento da análise
    cog_y        : desvio ântero-posterior
    deviation    : tipo de desvio (para máquina de estados)
    """
    message:    str
    should_speak: bool
    severity:   str
    cog_x:      float = 0.0
    cog_y:      float = 0.0
    deviation:  DeviationType = DeviationType.NONE


class Exercise(ABC):
    name:          str = "Exercício"
    start_message: str = "Exercício iniciado."
    end_message:   str = "Exercício concluído."

    @abstractmethod
    def analyze(self, cog_x: float, cog_y: float, total_kg: float) -> FeedbackResult:
        pass
