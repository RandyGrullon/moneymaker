# 🤖 Bot de Trading — MetaTrader 5 + Groq AI

Bot que opera con dinero **real** en MetaTrader 5, decide con IA (Groq) y
**aprende** de sus propios trades. Incluye un dashboard web donde se ve todo
lo que hace en vivo.

> ⚠️ **ADVERTENCIA**: este bot opera con dinero real. Pruébalo primero en una
> **cuenta demo** de MT5 (funciona exactamente igual) o con `TRADING_ENABLED=false`.
> El trading con IA puede perder dinero. Úsalo bajo tu propio riesgo.

## Cómo funciona

```
en paralelo, todo el tiempo:
  · Centro de noticias EN VIVO: ~15 fuentes RSS (ForexLive, FXStreet, BBC,
    CNBC, MarketWatch, Fed, BCE, CoinDesk, OilPrice...) + calendario
    económico (ForexFactory) con NFP, CPI, decisiones de tipos, etc.

cada LOOP_SECONDS:
  1. Lee velas e indicadores de cada símbolo desde MT5 (EMA, RSI Wilder, MACD,
     Bollinger, ADX/DI, niveles de 50 velas, spread, volumen, sesión horaria)
     + resumen de los timeframes superiores (p.ej. H1 y H4 si operas M5)
  2. Filtros previos baratos: spread vs ATR, cooldown por rachas de pérdidas,
     BLOQUEO si hay dato de alto impacto inminente, y solo se consulta a la
     IA cuando hay vela nueva (o movimiento brusco)
  3. La IA razona PASO A PASO (tendencia mayor, momentum, niveles, contexto,
     NOTICIAS en vivo, su propio historial, argumentos en contra) y recién
     entonces decide: buy / sell / hold + confianza + razón + SL/TP (× ATR).
     En su análisis cita QUÉ noticia o evento pesa en la decisión.
  4. Filtros de riesgo: confianza mínima (adaptativa), máx. posiciones,
     exposición por divisa, pérdida diaria
  5. Si pasa los filtros → orden REAL en MT5 con SL y TP
  6. Si hay posición abierta, la IA la gestiona cada ciclo (viendo también
     las noticias): mantener / cerrar / mover SL a break-even
  7. Cuando un trade se cierra → la IA escribe una "lección" comparando el
     contexto de apertura (incluidas las noticias que había) vs cierre; cada
     N cierres consolida todo en hasta 5 "reglas duraderas"
```

El aprendizaje es triple:
- **Lecciones**: la IA analiza cada trade cerrado (su razonamiento vs. el resultado real, incluyendo el contexto de salida y la duración) y guarda una conclusión que se inyecta en los prompts siguientes.
- **Reglas consolidadas**: cada `CONSOLIDATE_EVERY` cierres, la IA destila las lecciones en un set corto de reglas duraderas (menos ruido, más señal).
- **Adaptación de riesgo**: si el win rate baja, el bot exige automáticamente más confianza (global y por símbolo), y la IA ve una **tabla de calibración** con su acierto real por nivel de confianza declarado.

## Requisitos

- Windows (el paquete `MetaTrader5` de Python solo funciona en Windows)
- Terminal MetaTrader 5 instalado y con una cuenta logueada
- IA: una de las dos opciones:
  - API key de Groq (gratis en https://console.groq.com, límite de 100k tokens/día), o
  - [Ollama](https://ollama.com) instalado para IA local (sin límites de tokens)

## Instalación

```powershell
pip install -r requirements.txt
copy .env.example .env
# Edita .env: pon tu GROQ_API_KEY y revisa los símbolos/riesgo
python main.py
```

### IA local con Ollama (sin límites de tokens)

```powershell
winget install -e --id Ollama.Ollama
ollama pull qwen2.5:14b
```

Luego en el `.env`:

```
AI_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:14b
```

Abre **http://localhost:5000** para ver el dashboard.

## Correr en un VPS (24/7)

El mismo código funciona en local y en un VPS sin cambios. Requisito clave:
el paquete `MetaTrader5` de Python **solo funciona en Windows**, así que usa un
**VPS Windows** (los "Forex VPS" típicos sirven). Para la IA en un VPS usa
`AI_PROVIDER=groq` (ligero) o apunta `OLLAMA_URL` a otra máquina con GPU;
correr Ollama dentro de un VPS barato suele ser demasiado lento.

**Pasos en el VPS:**

```powershell
# 1. Instala Python 3.11+ y el terminal MetaTrader 5 (inicia sesión una vez)
# 2. Copia el proyecto y sus dependencias
pip install -r requirements.txt
copy .env.example .env

# 3. Edita el .env para acceso remoto:
#    UI_HOST=0.0.0.0
#    UI_PASSWORD=una_clave_fuerte        <- OBLIGATORIA (sin ella el bot no arranca)
#    GROQ_API_KEY=...                    (y el resto de tu config)

# 4. Instala el auto-arranque + auto-reinicio + firewall (una sola vez, como Admin)
powershell -ExecutionPolicy Bypass -File scripts\install_vps_task.ps1
Start-ScheduledTask -TaskName OmniTradeBot
```

Luego entra desde tu PC a `http://IP_DEL_VPS:5000` — te pedirá la contraseña
(`UI_PASSWORD`). La sesión dura 30 días por navegador.

**Qué hace el modo VPS por ti:**

- **Login obligatorio** en el panel cuando `UI_HOST` no es localhost (páginas y API).
- **Servidor de producción** (waitress) en vez del servidor de desarrollo de Flask.
- **Auto-reconexión a MT5**: si el terminal se cae o se reinicia, el bot
  reintenta cada 30 s con las credenciales del `.env`.
- **Auto-reinicio del bot**: `scripts/start_bot.ps1` relanza el proceso si
  crashea, y la tarea programada lo levanta al iniciar sesión en Windows.
- **Logs persistentes**: todo queda en `logs\bot.log` (eventos, rotativo) y
  `logs\console.log` (salida cruda del proceso).

**Importante en el VPS:**

- MT5 necesita sesión gráfica: al salir del RDP usa **Desconectar** (la X de la
  ventana), **no** "Cerrar sesión", para que el terminal y el bot sigan vivos.
- **Auto-logon** (recomendado): Windows Update puede reiniciar el VPS de
  madrugada, y la tarea arranca "al iniciar sesión". Activa el inicio de sesión
  automático (`netplwiz` → desmarca "Los usuarios deben escribir su nombre…";
  si no aparece esa casilla, usa la herramienta *Autologon* de Sysinternals)
  para que tras un reinicio el bot reviva solo.
- Si tu proveedor tiene **firewall externo / security group** (panel de
  control del VPS), abre ahí también el puerto TCP de la UI.
- El panel viaja en HTTP plano. Con contraseña fuerte y un puerto no estándar
  es razonable para uso personal; si quieres más, restringe el puerto a tu IP
  en el firewall del VPS o usa un túnel (Tailscale/WireGuard).
- Desinstalar: `scripts\uninstall_vps_task.ps1`.

Para correrlo **en local** no cambia nada: `python main.py` como siempre
(sin `UI_PASSWORD` no hay login y el panel solo se ve desde tu propia máquina).

## Dashboard

- **Dashboard**: estado, balance, equity, P&L flotante, P&L total del bot, win rate, curva de equity
- **Mente IA**: decisiones con su análisis paso a paso, reglas consolidadas, lecciones y calibración de confianza
- **Pensamientos**: registro completo de CADA llamada a la IA — el prompt exacto, el análisis paso a paso, la respuesta cruda, la latencia y qué hizo el bot con esa decisión (ejecutada / bloqueada y por qué)
- **Noticias**: titulares en vivo de ~15 fuentes con filtros por categoría y por símbolo, calendario económico de 48 h con impacto/previsión/previo, y la tarjeta "qué influye en cada símbolo AHORA" (lo mismo que ve la IA), con aviso de bloqueo por evento de alto impacto
- **Posiciones abiertas / historial / logs** en vivo
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
| `MAX_DAILY_LOSS_PCT` | Si se pierde este % en el día, no se abren más trades hasta mañana (las posiciones abiertas se siguen gestionando) |
| `MIN_CONFIDENCE` | Confianza mínima de la IA para abrir un trade |
| `SYMBOLS` | Símbolos a operar (usa los nombres exactos de tu broker) |
| `MAX_SPREAD_ATR_RATIO` | No abrir trades si el spread supera este múltiplo del ATR |
| `COOLDOWN_AFTER_LOSSES` / `COOLDOWN_MINUTES` | Tras N pérdidas seguidas en un símbolo, pausa de entradas en ese símbolo |
| `MAX_CURRENCY_EXPOSURE` | Máx. de posiciones apostando en la misma dirección de una divisa |
| `CONSOLIDATE_EVERY` | Cada cuántos cierres la IA consolida lecciones en reglas |
| `UI_HOST` | `127.0.0.1` = solo local · `0.0.0.0` = accesible desde fuera (VPS) |
| `UI_PASSWORD` | Contraseña del panel web (obligatoria si `UI_HOST` no es local) |
| `NEWS_ENABLED` | Centro de noticias en vivo + calendario económico para la IA |
| `NEWS_BLOCK_BEFORE_MIN` / `NEWS_BLOCK_AFTER_MIN` | No abrir trades alrededor de datos de alto impacto |
| `NEWS_EXTRA_FEEDS` | Feeds RSS extra tuyos (separados por coma) |

## Estructura

```
main.py              # arranque: MT5 + bot + UI
config.py            # configuración desde .env
app/mt5_client.py    # conexión MT5, datos multi-timeframe, órdenes reales
app/news.py          # noticias en vivo: ~15 RSS + calendario económico
app/ai_brain.py      # IA: decide, gestiona posiciones, aprende y consolida
app/memory.py        # SQLite: trades, lecciones, reglas, calibración
app/trader.py        # loop principal + filtros duros + gestión de riesgo
app/state.py         # estado compartido bot ↔ UI (incluye pensamientos)
ui/server.py         # API Flask + login del panel
ui/templates/        # dashboard, mente IA, pensamientos, posiciones...
scripts/             # arranque con auto-reinicio e instalador para VPS
logs/                # bot.log (eventos) y console.log (salida del proceso)
```
