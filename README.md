# 🤖 Bot de Trading — MetaTrader 5 + Groq AI

Bot que opera con dinero **real** en MetaTrader 5, decide con IA (Groq) y
**aprende** de sus propios trades. Incluye un dashboard web donde se ve todo
lo que hace en vivo.

> ⚠️ **ADVERTENCIA**: este bot opera con dinero real. Pruébalo primero en una
> **cuenta demo** de MT5 (funciona exactamente igual) o con `TRADING_ENABLED=false`.
> El trading con IA puede perder dinero. Úsalo bajo tu propio riesgo.

## Cómo funciona

```
cada LOOP_SECONDS:
  1. Lee velas e indicadores (EMA, RSI, ATR) de cada símbolo desde MT5
  2. Le pasa a Groq: mercado + estadísticas reales + lecciones aprendidas
  3. Groq responde: buy / sell / hold + confianza + razón + SL/TP
  4. Filtros de riesgo: confianza mínima, máx. posiciones, pérdida diaria
  5. Si pasa los filtros → orden REAL en MT5 con SL y TP
  6. Cuando un trade se cierra → la IA escribe una "lección" que se guarda
     en SQLite y se usa en todas las decisiones futuras (así aprende)
```

El aprendizaje es doble:
- **Lecciones**: la IA analiza cada trade cerrado (su razonamiento vs. el resultado real) y guarda una conclusión que se inyecta en los prompts siguientes.
- **Adaptación de riesgo**: si el win rate baja de 50%, el bot exige automáticamente más confianza para abrir trades.

## Requisitos

- Windows (el paquete `MetaTrader5` de Python solo funciona en Windows)
- Terminal MetaTrader 5 instalado y con una cuenta logueada
- API key de Groq (gratis en https://console.groq.com)

## Instalación

```powershell
pip install -r requirements.txt
copy .env.example .env
# Edita .env: pon tu GROQ_API_KEY y revisa los símbolos/riesgo
python main.py
```

Abre **http://localhost:5000** para ver el dashboard.

## Dashboard

- **Cabecera**: estado, balance, equity, P&L flotante, P&L total del bot, win rate
- **Decisiones de la IA**: qué decidió para cada símbolo y por qué
- **Lecciones aprendidas**: lo que la IA concluyó de cada trade cerrado
- **Posiciones abiertas / historial / registro de actividad** en vivo
- Botones **Pausar** y **Cerrar todo**

## Varias cuentas (cambio desde la UI)

Copia `accounts.json.example` a **`accounts.json`** y pon ahí tus cuentas
(demo y/o reales). En el dashboard aparece un selector **"Cambiar cuenta"** que
cambia de cuenta en caliente, sin reiniciar el bot. Todas deben ser del mismo
terminal MT5 instalado; el nombre del servidor debe ser exacto.

Notas:
- Al cambiar de cuenta, las posiciones de la cuenta anterior dejan de ser
  gestionadas por el bot, pero quedan protegidas por su SL/TP en el broker.
- El límite de pérdida diaria se recalcula con el balance de la cuenta nueva.
- ⚠️ `accounts.json` guarda contraseñas en texto plano: no lo subas a ningún
  repositorio ni lo compartas.

## Configuración clave (.env)

| Variable | Qué hace |
|---|---|
| `TRADING_ENABLED` | `false` = solo analiza, no manda órdenes (modo seguro para probar) |
| `RISK_PER_TRADE_PCT` | % del equity que se arriesga por trade (el lote se calcula solo) |
| `MAX_DAILY_LOSS_PCT` | Si se pierde este % en el día, el bot se pausa hasta mañana |
| `MIN_CONFIDENCE` | Confianza mínima de la IA para abrir un trade |
| `SYMBOLS` | Símbolos a operar (usa los nombres exactos de tu broker) |

## Estructura

```
main.py              # arranque: MT5 + bot + UI
config.py            # configuración desde .env
app/mt5_client.py    # conexión MT5, datos, órdenes reales
app/ai_brain.py      # Groq: decide y genera lecciones
app/memory.py        # SQLite: trades, lecciones, estadísticas
app/trader.py        # loop principal + gestión de riesgo
app/state.py         # estado compartido bot ↔ UI
ui/server.py         # API Flask
ui/templates/index.html  # dashboard
```
