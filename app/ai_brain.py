"""Cerebro IA con Groq: decide trades y aprende de los resultados.

El "aprendizaje" funciona asi:
 1. Antes de decidir, el prompt incluye las estadisticas reales del bot
    (win rate, profit por simbolo) y las ultimas lecciones guardadas.
 2. Cuando un trade se cierra, se le pide a la IA que escriba una leccion
    concreta comparando su razonamiento original con el resultado real.
 3. Esa leccion se guarda en SQLite y entra en los prompts siguientes.
"""
import json

from groq import Groq

import config

SYSTEM_PROMPT = """Eres un trader profesional y disciplinado que opera en MetaTrader 5.
Recibes datos de mercado con indicadores, tus estadisticas reales y lecciones de tus
trades pasados. Decide si abrir un trade o no.

Reglas:
- Solo recomienda buy/sell si hay una senal clara. Si hay duda, responde hold.
- Usa las lecciones aprendidas: si algo te salio mal antes en condiciones parecidas, evitalo.
- Si tu win rate en un simbolo es malo, se mas exigente con ese simbolo.
- stop_loss_atr y take_profit_atr son multiplicadores del ATR (SL tipico 1.5-2.5, TP 2-4).
- El take profit debe ser mayor que el stop loss (ratio riesgo/beneficio >= 1.3).

Responde SOLO con JSON valido, sin texto extra:
{"action": "buy" | "sell" | "hold",
 "confidence": 0-100,
 "stop_loss_atr": numero,
 "take_profit_atr": numero,
 "reason": "explicacion breve en espanol (max 200 caracteres)"}"""

LESSON_PROMPT = """Eres un trader que revisa sus operaciones cerradas para mejorar.
Te doy un trade que acabas de cerrar: tu razonamiento original, el contexto de mercado
que viste, y el resultado real en dinero.

Escribe UNA leccion corta, concreta y accionable (max 200 caracteres, en espanol) que
te ayude a decidir mejor la proxima vez en condiciones parecidas. No seas generico:
referencia las condiciones especificas (RSI, tendencia, simbolo, hora, etc).

Responde SOLO con JSON: {"lesson": "..."}"""


class AIBrain:
    def __init__(self):
        self.client = Groq(api_key=config.GROQ_API_KEY)
        self.model = config.GROQ_MODEL

    def _chat(self, system: str, user: str) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=400,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)

    def decide(self, snapshot: dict, stats: dict, lessons: list[dict],
               open_positions: list[dict]) -> dict:
        """Pide a Groq una decision de trading para un simbolo."""
        lessons_text = "\n".join(
            f"- [{l['symbol']}] {l['lesson']}" for l in lessons
        ) or "(todavia no hay lecciones)"

        user = f"""DATOS DE MERCADO ({snapshot['symbol']} {snapshot['timeframe']}):
{json.dumps(snapshot, ensure_ascii=False)}

MIS ESTADISTICAS REALES:
{json.dumps(stats, ensure_ascii=False)}

POSICIONES ABIERTAS AHORA: {len(open_positions)}
{json.dumps(open_positions, ensure_ascii=False)}

LECCIONES QUE APRENDI DE TRADES ANTERIORES:
{lessons_text}

Decide para {snapshot['symbol']}."""

        decision = self._chat(SYSTEM_PROMPT, user)

        # Validacion defensiva: la IA puede devolver cualquier cosa
        action = str(decision.get("action", "hold")).lower()
        if action not in ("buy", "sell", "hold"):
            action = "hold"
        return {
            "action": action,
            "confidence": max(0, min(100, int(decision.get("confidence", 0)))),
            "stop_loss_atr": max(0.5, min(5.0, float(decision.get("stop_loss_atr", 2.0)))),
            "take_profit_atr": max(0.5, min(10.0, float(decision.get("take_profit_atr", 3.0)))),
            "reason": str(decision.get("reason", ""))[:300],
        }

    def learn_from_trade(self, trade: dict, profit: float) -> str:
        """Genera una leccion a partir de un trade cerrado."""
        outcome = "GANANCIA" if profit > 0 else "PERDIDA"
        user = f"""TRADE CERRADO:
- Simbolo: {trade['symbol']} | Direccion: {trade['direction']}
- Mi razonamiento al abrir: {trade['reason']}
- Mi confianza era: {trade['confidence']}/100
- Contexto de mercado al abrir: {trade['market_context']}
- RESULTADO: {outcome} de {profit:.2f}

Escribe la leccion."""
        result = self._chat(LESSON_PROMPT, user)
        return str(result.get("lesson", ""))[:300]
