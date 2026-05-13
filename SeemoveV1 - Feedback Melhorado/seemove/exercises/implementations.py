"""
exercises/implementations.py

Sistema de feedback instrucional para pacientes em reabilitação.

Princípios:
  1. Uma instrução por vez — a mais urgente, nunca uma lista
  2. Linguagem corporal simples e direta: "Transfira o peso para a perna esquerda"
  3. Confirmação positiva quando corrige: "Isso, muito bom"
  4. Reforço progressivo se não corrigir: instrução + dica adicional
  5. Silêncio quando tudo está certo — não "encher linguiça"
"""

import math
from exercises.base import Exercise, FeedbackResult, DeviationType


# ─── Agachamento ─────────────────────────────────────────────────────────────

class SquatExercise(Exercise):
    """
    Agachamento bipodal.

    O CoG se desloca naturalmente para frente durante o agachamento,
    então o eixo Y tem limiar mais generoso que o X.
    O problema mais crítico para o paciente é o desvio lateral
    (indica joelho valgo ou diferença de força entre pernas).
    """
    name          = "Agachamento"
    start_message = "Vamos começar o agachamento. Posicione os pés na largura dos ombros e mantenha o peso igual nas duas pernas."
    end_message   = "Agachamento encerrado. Descanse."

    THRESHOLD_X      = 0.12   # desvio lateral inaceitável
    THRESHOLD_X_MILD = 0.07   # desvio leve — instrução suave
    THRESHOLD_Y      = 0.22   # desvio ântero-posterior

    # Frases por intensidade do desvio lateral
    LATERAL_MILD = {
        "right": "Transfira um pouco mais de peso para a perna esquerda.",
        "left":  "Transfira um pouco mais de peso para a perna direita.",
    }
    LATERAL_STRONG = {
        "right": "Seu peso está muito concentrado na perna direita. Empurre o joelho esquerdo levemente para fora e distribua o peso.",
        "left":  "Seu peso está muito concentrado na perna esquerda. Empurre o joelho direito levemente para fora e distribua o peso.",
    }
    ANTEROPOSTERIOR = {
        "forward":  "Você está se inclinando muito para frente. Recue o quadril e mantenha o tronco ereto.",
        "backward": "Você está com o peso muito atrás. Incline levemente o tronco para frente e deixe os joelhos avançarem.",
    }

    def analyze(self, cog_x, cog_y, total_kg) -> FeedbackResult:
        if total_kg < 20:
            return FeedbackResult(
                "Suba na plataforma com os dois pés para começarmos.",
                True, "warn", cog_x, cog_y, DeviationType.NO_WEIGHT)

        ax, ay = abs(cog_x), abs(cog_y)

        # Prioridade 1: desvio lateral forte
        if ax > self.THRESHOLD_X:
            side = "right" if cog_x > 0 else "left"
            dev  = DeviationType.RIGHT if side == "right" else DeviationType.LEFT
            sev  = "error" if ax > 0.28 else "warn"
            msg  = self.LATERAL_STRONG[side] if ax > 0.22 else self.LATERAL_MILD[side]
            return FeedbackResult(msg, True, sev, cog_x, cog_y, dev)

        # Prioridade 2: desvio leve — só instrução suave
        if ax > self.THRESHOLD_X_MILD and ax >= ay:
            side = "right" if cog_x > 0 else "left"
            dev  = DeviationType.RIGHT if side == "right" else DeviationType.LEFT
            return FeedbackResult(
                self.LATERAL_MILD[side], True, "warn", cog_x, cog_y, dev)

        # Prioridade 3: desvio ântero-posterior
        if ay > self.THRESHOLD_Y:
            direction = "forward" if cog_y > 0 else "backward"
            dev = DeviationType.FORWARD if direction == "forward" else DeviationType.BACKWARD
            return FeedbackResult(
                self.ANTEROPOSTERIOR[direction], True, "warn", cog_x, cog_y, dev)

        return FeedbackResult("", False, "ok", cog_x, cog_y, DeviationType.NONE)


# ─── Equilíbrio Unipodial ────────────────────────────────────────────────────

class UnipodialBalanceExercise(Exercise):
    """
    Equilíbrio em uma perna.

    O desafio é manter o tronco estável enquanto o CoG oscila.
    O feedback foca em estratégias concretas de estabilização,
    não apenas em "você está desequilibrado".
    """
    name          = "Equilíbrio unipodial"
    start_message = "Vamos ao equilíbrio em uma perna. Encontre um ponto fixo à sua frente para olhar. Quando estiver pronto, eleve uma perna devagar."
    end_message   = "Equilíbrio encerrado. Bom trabalho."

    THRESHOLD_OK      = 0.20  # dentro desse raio, está bem
    THRESHOLD_WARN    = 0.38  # oscilação moderada
    THRESHOLD_CRITICAL = 0.55  # risco de queda

    INSTRUCTIONS = {
        "mild":     "Você está oscilando um pouco. Contraia o abdômen e fixe o olhar em um ponto na sua frente.",
        "moderate": "A oscilação está aumentando. Pressione o pé de apoio contra o chão e respire fundo.",
        "critical": "Oscilação muito grande. Apoie as duas pernas agora para não cair.",
    }

    def analyze(self, cog_x, cog_y, total_kg) -> FeedbackResult:
        mag = math.sqrt(cog_x**2 + cog_y**2)

        if mag > self.THRESHOLD_CRITICAL:
            return FeedbackResult(
                self.INSTRUCTIONS["critical"],
                True, "error", cog_x, cog_y, DeviationType.CRITICAL)

        if mag > self.THRESHOLD_WARN:
            return FeedbackResult(
                self.INSTRUCTIONS["moderate"],
                True, "warn", cog_x, cog_y, DeviationType.INSTABLE)

        if mag > self.THRESHOLD_OK:
            return FeedbackResult(
                self.INSTRUCTIONS["mild"],
                True, "warn", cog_x, cog_y, DeviationType.INSTABLE)

        return FeedbackResult("", False, "ok", cog_x, cog_y, DeviationType.NONE)


# ─── Postura Estática ────────────────────────────────────────────────────────

class StaticPostureExercise(Exercise):
    """
    Avaliação postural em pé.

    Limiares mais rígidos — não há movimento intencional.
    O feedback é lento e calmo: o paciente precisa de tempo para
    perceber e ajustar a postura sem se apressar.
    """
    name          = "Postura estática"
    start_message = "Fique em pé de forma natural, braços ao lado do corpo. Vamos avaliar sua postura."
    end_message   = "Avaliação postural encerrada."

    THRESHOLD_X = 0.08
    THRESHOLD_Y = 0.10

    LATERAL = {
        "right": "Você está com mais peso na perna direita. Distribua o peso igualmente entre as duas pernas.",
        "left":  "Você está com mais peso na perna esquerda. Distribua o peso igualmente entre as duas pernas.",
    }
    ANTEROPOSTERIOR = {
        "forward":  "Você está inclinado para frente. Afaste levemente os quadris para trás e alinhe a cabeça com a coluna.",
        "backward": "Você está com o peso muito nos calcanhares. Desloque levemente o peso para a frente dos pés.",
    }

    def analyze(self, cog_x, cog_y, total_kg) -> FeedbackResult:
        ax, ay = abs(cog_x), abs(cog_y)

        if ax > self.THRESHOLD_X and ax >= ay:
            side = "right" if cog_x > 0 else "left"
            dev  = DeviationType.RIGHT if side == "right" else DeviationType.LEFT
            sev  = "error" if ax > 0.20 else "warn"
            return FeedbackResult(self.LATERAL[side], True, sev, cog_x, cog_y, dev)

        if ay > self.THRESHOLD_Y:
            direction = "forward" if cog_y > 0 else "backward"
            dev = DeviationType.FORWARD if direction == "forward" else DeviationType.BACKWARD
            return FeedbackResult(
                self.ANTEROPOSTERIOR[direction], True, "warn", cog_x, cog_y, dev)

        return FeedbackResult("", False, "ok", cog_x, cog_y, DeviationType.NONE)


# ─── Avanço (Lunge) ──────────────────────────────────────────────────────────

class LungeExercise(Exercise):
    """
    Avanço frontal.

    O CoG se desloca significativamente para frente — isso é esperado.
    O problema principal é o desvio lateral (rotação de tronco ou
    joelho que cede para dentro).
    """
    name          = "Avanço (lunge)"
    start_message = "Vamos ao avanço. Posicione um pé à frente. Mantenha o tronco ereto e o joelho dianteiro alinhado com o pé."
    end_message   = "Avanços encerrados."

    THRESHOLD_X = 0.18

    LATERAL = {
        "right": "Seu tronco está girando para a direita. Alinhe os ombros com o quadril e olhe para frente.",
        "left":  "Seu tronco está girando para a esquerda. Alinhe os ombros com o quadril e olhe para frente.",
    }

    def analyze(self, cog_x, cog_y, total_kg) -> FeedbackResult:
        ax = abs(cog_x)

        if ax > self.THRESHOLD_X:
            side = "right" if cog_x > 0 else "left"
            dev  = DeviationType.RIGHT if side == "right" else DeviationType.LEFT
            sev  = "error" if ax > 0.32 else "warn"
            return FeedbackResult(self.LATERAL[side], True, sev, cog_x, cog_y, dev)

        if cog_y < -0.42:
            return FeedbackResult(
                "Você está com o peso muito para trás. Incline o tronco levemente para frente.",
                True, "warn", cog_x, cog_y, DeviationType.BACKWARD)

        return FeedbackResult("", False, "ok", cog_x, cog_y, DeviationType.NONE)
