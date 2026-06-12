"""Cerebro IA (Groq u Ollama local): decide trades, gestiona posiciones y aprende.

Como piensa:
 1. Cada prompt obliga a la IA a escribir un analisis paso a paso ("analysis")
    ANTES de decidir: mejora la calidad de la decision y queda registrado.
 2. Antes de decidir recibe: mercado multi-timeframe con indicadores, sesion,
    sus estadisticas reales, la calibracion de su confianza, sus reglas
    consolidadas y las lecciones de trades pasados.
 3. Cada trade cerrado genera una leccion (comparando apertura vs cierre);
    cada N cierres las lecciones se consolidan en reglas duraderas.
 4. TODO lo que entra y sale de la IA se registra en state.thoughts y se ve
    en la pagina "Pensamientos" de la UI.
"""
import json
import time
from datetime import datetime

import config
from app.state import state

if config.AI_PROVIDER == "ollama":
    from openai import OpenAI
else:
    from groq import Groq

DECISION_PROMPT = """Eres un trader profesional y disciplinado que opera en MetaTrader 5.
Gestionas una cuenta real: tu UNICO trabajo es MULTIPLICAR ese capital de forma
sostenida. Proteger el capital es parte de multiplicarlo: una mala racha que
reduce mucho la cuenta destruye semanas de ganancias.

Antes de decidir DEBES razonar paso a paso rellenando "analysis":
1. tendencia_mayor: ¿que dicen los timeframes superiores (higher_timeframes)?
   ¿La operacion iria a favor o en contra de la tendencia mayor? Operar contra
   ella exige senales mucho mas fuertes.
2. momentum: RSI, MACD, ADX y DI+/DI-: ¿hay fuerza direccional real o el
   mercado esta lateral (ADX < 20)? ¿El volumen acompana?
3. niveles: ¿el precio esta pegado al maximo/minimo de 50 velas o a una banda
   de Bollinger? ¿Tiene el TP espacio para respirar antes del proximo nivel?
4. contexto: sesion actual y liquidez, spread_vs_atr, dia de la semana.
   Spread alto, viernes por la noche o mercado sin sesion = peores condiciones.
5. noticias: revisa los titulares EN VIVO y el calendario economico. ¿Alguna
   noticia o evento afecta a este simbolo AHORA? CITA cual y como pesa en tu
   decision (o di explicitamente que ninguna es relevante). Una noticia fuerte
   puede invalidar una senal tecnica perfecta.
6. mi_historial: usa tus estadisticas, tu calibracion de confianza, tus reglas
   y lecciones. Si tu win rate en este simbolo es malo, se mas exigente.
7. argumentos_contra: el mejor argumento PARA NO operar. Si pesa mas que la
   senal, responde hold.

Reglas:
- Solo buy/sell con senal clara Y coherente con tu analisis. Ante la duda: hold.
- Un hold no cuesta nada; una mala entrada si.
- NUNCA abras una posicion nueva minutos antes de un dato de ALTO impacto de
  las divisas del simbolo (NFP, CPI, decision de tipos...): el spread se dispara
  y la volatilidad barre los stops. Tras el dato, espera a que el mercado lo
  digiera antes de entrar.
- Si el equity esta por debajo del balance (perdida flotante) o el margen libre
  es bajo, se mas conservador.
- stop_loss_atr y take_profit_atr son multiplicadores del ATR (SL tipico
  1.5-2.5, TP 2-4). El ratio TP/SL debe ser >= 1.3 siempre.
- confidence honesta: tu tabla de calibracion muestra como acerto cada nivel de
  confianza en el pasado; ajustate a ella.

Responde SOLO con JSON valido, sin texto extra, con las claves EN ESTE ORDEN:
{"analysis": {"tendencia_mayor": "...", "momentum": "...", "niveles": "...",
  "contexto": "...", "noticias": "...", "mi_historial": "...",
  "argumentos_contra": "..."},
 "action": "buy" | "sell" | "hold",
 "confidence": 0-100,
 "stop_loss_atr": numero,
 "take_profit_atr": numero,
 "reason": "resumen ejecutivo en espanol (max 200 caracteres)"}"""

MANAGE_PROMPT = """Eres un trader profesional gestionando UNA posicion abierta en MetaTrader 5.
Decide que hacer con ella AHORA. No puedes abrir posiciones nuevas.

Acciones posibles:
- "hold": dejarla correr (no cierres por ruido pequeno; el SL/TP ya la protegen).
- "close": cerrar YA a mercado (la tesis original se invalido o hay senal
  contraria clara y fuerte).
- "breakeven": mover el stop loss al precio de entrada para eliminar el riesgo
  (solo si va claramente a favor pero aun queda camino hasta el take profit).

Antes de decidir razona en "analysis":
1. tesis_original: ¿sigue viva la razon por la que abri esta posicion?
2. evolucion: ¿como ha ido el precio respecto a la entrada, el SL y el TP?
3. noticias: ¿hay titulares o eventos del calendario que afecten a esta
   posicion AHORA? Si viene un dato de ALTO impacto en contra y hay beneficio,
   protegerlo (breakeven o close) suele ser mejor que apostar al dato.
4. riesgo_actual: ¿que pierdo si aguanto vs que gano si actuo ya?

Responde SOLO con JSON valido, sin texto extra, claves EN ESTE ORDEN:
{"analysis": {"tesis_original": "...", "evolucion": "...", "noticias": "...",
  "riesgo_actual": "..."},
 "action": "hold" | "close" | "breakeven",
 "confidence": 0-100,
 "reason": "resumen breve en espanol (max 200 caracteres)"}"""

LESSON_PROMPT = """Eres un trader que revisa sus operaciones cerradas para mejorar.
Te doy un trade cerrado: mi razonamiento original, el contexto de mercado al
abrir, el contexto al cerrar, la duracion y el resultado real en dinero.

Escribe UNA leccion corta, concreta y accionable (max 200 caracteres, espanol)
que me ayude a decidir mejor en condiciones parecidas. No seas generico:
referencia condiciones especificas (RSI, tendencia mayor, sesion, spread,
duracion, simbolo, hora...). Si fue ganancia por suerte (el mercado me dio la
razon por motivos ajenos a mi analisis), dilo: una ganancia con mal proceso NO
valida el proceso.

Responde SOLO con JSON: {"lesson": "..."}"""

CONSOLIDATE_PROMPT = """Eres un trader que destila su diario de trading.
Te doy mis estadisticas reales y mis ultimas lecciones (pueden repetirse o
contradecirse entre si). Resume todo en MAXIMO 5 reglas duraderas, concretas y
accionables (espanol, max 180 caracteres cada una). Prioriza patrones que se
repiten y reglas que protejan el capital. Descarta el ruido de trades sueltos.

Responde SOLO con JSON: {"rules": ["...", "..."]}"""


class AIBrain:
    def __init__(self):
        if config.AI_PROVIDER == "ollama":
            # Ollama expone un endpoint compatible con OpenAI; la api_key es ignorada
            self.client = OpenAI(base_url=config.OLLAMA_URL, api_key="ollama", timeout=180)
            self.model = config.OLLAMA_MODEL
        else:
            self.client = Groq(api_key=config.GROQ_API_KEY, timeout=90)
            self.model = config.GROQ_MODEL

    # ---------- Infraestructura ----------

    def _ask(self, phase: str, symbol: str, system: str, user: str,
             max_tokens: int) -> tuple[dict | None, int]:
        """Llama a la IA y registra TODO en state.thoughts.

        Devuelve (json_parseado_o_None, thought_id). Nunca lanza: un fallo de
        la IA en un simbolo no debe tumbar el ciclo entero del bot.
        """
        t0 = time.time()
        raw, error, parsed = "", "", None
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            error = f"la IA no devolvio JSON valido: {e}"
        except Exception as e:
            error = f"error llamando a la IA: {e}"

        thought_id = state.add_thought({
            "phase": phase,
            "symbol": symbol,
            "model": f"{self.model} ({config.AI_PROVIDER})",
            "latency_s": round(time.time() - t0, 1),
            "system_prompt": system,
            "user_prompt": user,
            "raw_response": raw,
            "parsed": parsed if isinstance(parsed, dict) else None,
            "error": error,
        })
        if error:
            state.log("error", f"IA [{phase}{' ' + symbol if symbol else ''}]: {error}")
        return (parsed if isinstance(parsed, dict) else None), thought_id

    @staticmethod
    def _num(data: dict, key: str, default: float) -> float:
        try:
            return float(data.get(key))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clean_analysis(parsed: dict) -> dict:
        analysis = parsed.get("analysis")
        if not isinstance(analysis, dict):
            return {}
        return {str(k): str(v)[:600] for k, v in analysis.items() if v}

    @staticmethod
    def _account_text(account: dict | None) -> str:
        account = account or {}
        return (
            f"- Balance: {account.get('balance', '?')} {account.get('currency', '')}\n"
            f"- Equity (valor real ahora): {account.get('equity', '?')}\n"
            f"- Margen libre (disponible para operar): {account.get('margin_free', '?')}\n"
            f"- P&L flotante: {account.get('profit', '?')}"
        )

    @staticmethod
    def _calibration_text(calibration: list[dict]) -> str:
        rows = [c for c in calibration if c.get("n")]
        if not rows:
            return "(aun sin datos: no hay trades cerrados)"
        return "\n".join(
            f"- Cuando declaraste {c['bucket']} de confianza: {c['n']} trades, "
            f"ganaste el {c['win_rate']}% (P&L {c['profit']})"
            for c in rows
        )

    @staticmethod
    def _lessons_text(lessons: dict) -> str:
        lines = [f"- [{l['symbol']}] {l['lesson']}"
                 for l in lessons.get("symbol", []) + lessons.get("others", [])]
        return "\n".join(lines) or "(todavia no hay lecciones)"

    @staticmethod
    def _news_text(news: dict | None) -> str:
        if not news:
            return "(modulo de noticias desactivado o aun cargando)"
        parts = []
        heads = news.get("headlines") or []
        parts.append("Titulares EN VIVO (mas recientes primero):\n"
                     + ("\n".join(f"- {h}" for h in heads) or "- (sin titulares todavia)"))
        upcoming = news.get("upcoming_events") or []
        parts.append("Eventos economicos PROXIMOS de las divisas de este simbolo:\n"
                     + ("\n".join(f"- {e}" for e in upcoming)
                        or "- (ninguno en las proximas 12 horas)"))
        recent = news.get("recent_events") or []
        if recent:
            parts.append("Eventos RECIENTES (el mercado puede seguir digiriendolos):\n"
                         + "\n".join(f"- {e}" for e in recent))
        return "\n".join(parts)

    @staticmethod
    def _duration_text(opened_at, closed_at) -> str:
        try:
            delta = datetime.fromisoformat(closed_at) - datetime.fromisoformat(opened_at)
            minutes = max(0, int(delta.total_seconds() // 60))
            return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"
        except (TypeError, ValueError):
            return "?"

    # ---------- Decision de entrada ----------

    def decide(self, snapshot: dict, context: dict) -> tuple[dict, int]:
        """Pide a la IA una decision de entrada. Devuelve (decision, thought_id)."""
        open_positions = context.get("open_positions", [])
        rules = context.get("rules", [])
        rules_text = "\n".join(f"- {r}" for r in rules) or "(aun no hay reglas consolidadas)"
        snapshot_for_ai = {k: v for k, v in snapshot.items() if k != "bar_time"}

        user = f"""MI CUENTA (tu trabajo es multiplicar este capital):
{self._account_text(context.get('account'))}

DATOS DE MERCADO ({snapshot['symbol']} {snapshot['timeframe']}; "higher_timeframes" = tendencia mayor):
{json.dumps(snapshot_for_ai, ensure_ascii=False)}

NOTICIAS Y CALENDARIO ECONOMICO EN VIVO:
{self._news_text(context.get('news'))}

MIS ESTADISTICAS REALES:
{json.dumps(context.get('stats', {}), ensure_ascii=False)}

CALIBRACION DE MI CONFIANZA (resultado real por nivel de confianza declarado):
{self._calibration_text(context.get('calibration', []))}

MIS REGLAS CONSOLIDADAS (destiladas de muchos trades):
{rules_text}

LECCIONES RECIENTES:
{self._lessons_text(context.get('lessons', {}))}

POSICIONES ABIERTAS AHORA ({len(open_positions)}):
{json.dumps(open_positions, ensure_ascii=False)}

Analiza paso a paso y decide para {snapshot['symbol']}."""

        parsed, thought_id = self._ask("decision", snapshot["symbol"],
                                       DECISION_PROMPT, user, max_tokens=1400)
        parsed = parsed or {}
        action = str(parsed.get("action", "hold")).lower()
        if action not in ("buy", "sell", "hold"):
            action = "hold"
        decision = {
            "action": action,
            "confidence": int(max(0, min(100, self._num(parsed, "confidence", 0)))),
            "stop_loss_atr": max(0.5, min(5.0, self._num(parsed, "stop_loss_atr", 2.0))),
            "take_profit_atr": max(0.5, min(10.0, self._num(parsed, "take_profit_atr", 3.0))),
            "reason": str(parsed.get("reason", "") or "(respuesta de la IA invalida)")[:300],
            "analysis": self._clean_analysis(parsed),
        }
        return decision, thought_id

    # ---------- Gestion de una posicion abierta ----------

    def manage(self, snapshot: dict, position: dict, original_trade: dict | None,
               account: dict | None, news: dict | None = None) -> tuple[dict, int]:
        """Pide a la IA que gestione una posicion abierta: hold / close / breakeven."""
        original = original_trade or {}
        atr = snapshot.get("atr_14") or 0
        current = position["price_current"]
        sl, tp = position["sl"], position["tp"]
        dist_sl = round(abs(current - sl) / atr, 2) if atr and sl else "?"
        dist_tp = round(abs(tp - current) / atr, 2) if atr and tp else "?"
        snapshot_for_ai = {k: v for k, v in snapshot.items() if k != "bar_time"}

        user = f"""MI POSICION ABIERTA:
- {position['symbol']} {position['direction'].upper()} {position['volume']} lotes
- Entrada: {position['price_open']} | Precio actual: {current} | P&L flotante: {position['profit']}
- SL: {sl or 'sin SL'} | TP: {tp or 'sin TP'}
- Distancia al SL: {dist_sl} ATR | Distancia al TP: {dist_tp} ATR
- Abierta desde: {original.get('opened_at', '?')}
- Mi tesis al abrir (confianza {original.get('confidence', '?')}%): {original.get('reason', '(desconocida)')}

MI CUENTA:
{self._account_text(account)}

MERCADO AHORA ({snapshot['symbol']} {snapshot['timeframe']}; "higher_timeframes" = tendencia mayor):
{json.dumps(snapshot_for_ai, ensure_ascii=False)}

NOTICIAS Y CALENDARIO ECONOMICO EN VIVO:
{self._news_text(news)}

¿Que hago con esta posicion AHORA?"""

        parsed, thought_id = self._ask("gestion", snapshot["symbol"],
                                       MANAGE_PROMPT, user, max_tokens=800)
        parsed = parsed or {}
        action = str(parsed.get("action", "hold")).lower()
        if action not in ("hold", "close", "breakeven"):
            action = "hold"
        decision = {
            "action": action,
            "confidence": int(max(0, min(100, self._num(parsed, "confidence", 0)))),
            "reason": str(parsed.get("reason", "") or "(respuesta de la IA invalida)")[:300],
            "analysis": self._clean_analysis(parsed),
        }
        return decision, thought_id

    # ---------- Aprendizaje ----------

    def learn_from_trade(self, trade: dict, profit: float,
                         exit_snapshot: dict | None = None) -> tuple[str, int]:
        """Genera una leccion a partir de un trade cerrado. Devuelve (leccion, thought_id)."""
        outcome = "GANANCIA" if profit > 0 else "PERDIDA"
        duration = self._duration_text(trade.get("opened_at"), trade.get("closed_at"))
        exit_text = "(no disponible)"
        if exit_snapshot:
            slim = {k: v for k, v in exit_snapshot.items()
                    if k not in ("bar_time", "last_5_candles", "last_10_closes")}
            exit_text = json.dumps(slim, ensure_ascii=False)

        user = f"""TRADE CERRADO:
- Simbolo: {trade['symbol']} | Direccion: {trade['direction']} | Duracion: {duration}
- Mi razonamiento al abrir: {trade['reason']}
- Mi confianza era: {trade['confidence']}/100
- Contexto de mercado al abrir: {trade['market_context']}
- Contexto de mercado al cerrar: {exit_text}
- RESULTADO: {outcome} de {profit:.2f}

Escribe la leccion."""
        parsed, thought_id = self._ask("leccion", trade["symbol"],
                                       LESSON_PROMPT, user, max_tokens=300)
        lesson = str((parsed or {}).get("lesson", ""))[:300]
        return lesson, thought_id

    def consolidate_lessons(self, stats: dict, lessons: list[dict]) -> tuple[list[str], int]:
        """Destila las lecciones acumuladas en un set corto de reglas duraderas."""
        lessons_text = "\n".join(
            f"- [{l['symbol']}] ({'GANANCIA' if (l.get('from_profit') or 0) > 0 else 'PERDIDA'}"
            f" {l.get('from_profit')}) {l['lesson']}"
            for l in lessons
        ) or "(sin lecciones)"
        user = f"""MIS ESTADISTICAS REALES:
{json.dumps(stats, ensure_ascii=False)}

MIS ULTIMAS LECCIONES:
{lessons_text}

Destila las reglas."""
        parsed, thought_id = self._ask("consolidacion", "",
                                       CONSOLIDATE_PROMPT, user, max_tokens=500)
        rules = (parsed or {}).get("rules")
        if not isinstance(rules, list):
            return [], thought_id
        return [str(r).strip()[:250] for r in rules if str(r).strip()][:5], thought_id
