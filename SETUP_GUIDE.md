# 🛠️ Guía de Configuración y Uso — Moneymaker Bot

Esta guía detalla paso a paso cómo configurar, inicializar y operar de forma segura el bot de trading automático que integra **MetaTrader 5** e Inteligencia Artificial (**Groq AI**).

---

## 📋 Requisitos Previos

Antes de comenzar, asegúrate de contar con lo siguiente:

1. **Sistema Operativo Windows**: La librería oficial `MetaTrader5` solo es compatible con entornos Windows.
2. **MetaTrader 5**: El terminal instalado en tu sistema.
3. **Cuenta de Broker**: Una cuenta demo o real configurada en el terminal de MetaTrader 5.
4. **Clave API de Groq**: Regístrate y crea una clave en la [Consola de Groq](https://console.groq.com/).
5. **Python 3.10 o Superior**: Instalado en tu equipo. Asegúrate de marcar la opción "Add Python to PATH" durante la instalación.

---

## 🚀 Instalación Paso a Paso

Sigue estos pasos en tu terminal (PowerShell o CMD) dentro del directorio raíz del proyecto:

### 1. Crear y activar un entorno virtual (Recomendado)
Para evitar conflictos de dependencias con otros proyectos:

```powershell
# Crear el entorno virtual
python -m venv venv

# Activar el entorno virtual
venv\Scripts\activate
```

### 2. Instalar las dependencias
Instala los paquetes necesarios definidos en `requirements.txt`:

```powershell
pip install -r requirements.txt
```

### 3. Configurar las Variables de Entorno (`.env`)
Duplica el archivo de ejemplo para crear tu configuración personal:

```powershell
copy .env.example .env
```

Abre el nuevo archivo `.env` en tu editor de texto y rellena los siguientes campos obligatorios:

```ini
# ===== GROQ =====
# Tu clave de API obtenida de https://console.groq.com
GROQ_API_KEY=gsk_tu_api_key_aqui
# Modelo a utilizar (se recomienda mantener el por defecto)
GROQ_MODEL=llama-3.3-70b-versatile

# ===== METATRADER 5 =====
# Si dejas estos vacíos, el bot se conectará a la cuenta activa en tu MT5 abierto.
# Si deseas automatizar el login, completa los datos:
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
# Opcional: Ruta absoluta al archivo terminal64.exe (ej: C:\Program Files\MetaTrader 5\terminal64.exe)
MT5_PATH=
```

---

## ⚙️ Parámetros de Operación y Gestión de Riesgo

El bot viene pre-configurado con parámetros de control de riesgo rigurosos. Puedes modificarlos en el archivo `.env`:

| Parámetro | Valor por Defecto | Descripción |
|---|---|---|
| `SYMBOLS` | `EURUSD,GBPUSD,XAUUSD` | Lista de símbolos a operar (deben coincidir exactamente con el nombre en tu broker). |
| `TIMEFRAME` | `M5` | Temporalidad de análisis de velas (ej. `M5` = 5 minutos, `H1` = 1 hora). |
| `LOOP_SECONDS` | `60` | Segundos de espera entre cada ciclo de análisis del bot. |
| `RISK_PER_TRADE_PCT` | `0.5` | Porcentaje del balance/patrimonio arriesgado por cada transacción (para cálculo de lotaje). |
| `MAX_OPEN_POSITIONS` | `3` | Número máximo de posiciones simultáneas que el bot puede mantener abiertas. |
| `MAX_DAILY_LOSS_PCT` | `3.0` | Si la pérdida diaria acumulada supera este porcentaje, el bot detiene sus operaciones automáticas. |
| `MIN_CONFIDENCE` | `65` | Nivel mínimo de confianza (0 a 100) que el modelo de IA debe otorgar al trade para ejecutarlo. |
| `TRADING_ENABLED` | `true` | Si se establece en `false`, el bot funcionará en modo **Paper Trading** (análisis en tiempo real sin mandar órdenes). |
| `UI_PORT` | `5000` | Puerto en el que se ejecuta la interfaz web local. |

---

## 👥 Configuración de Cuentas Múltiples (Opcional)

Si manejas múltiples cuentas (por ejemplo, una Demo para pruebas y una Real), puedes configurarlas para cambiar de cuenta directamente desde el Dashboard web en caliente, sin reiniciar el bot.

1. Copia el archivo `accounts.json.example`:
   ```powershell
   copy accounts.json.example accounts.json
   ```
2. Edita `accounts.json` con los detalles de tus cuentas:
   ```json
   [
     {
       "name": "Mi Cuenta Demo",
       "login": 12345678,
       "password": "mi_password_seguro",
       "server": "BrokerServer-Demo"
     },
     {
       "name": "Mi Cuenta Real",
       "login": 87654321,
       "password": "mi_password_real",
       "server": "BrokerServer-Real"
     }
   ]
   ```

> [!WARNING]
> El archivo `accounts.json` contiene contraseñas en texto plano. **Nunca lo compartas ni lo subas a repositorios públicos**. Ya está agregado en el archivo `.gitignore` para tu protección.

---

## 🚦 Ejecución del Bot

Una vez configurado todo, sigue estos pasos para iniciar el bot:

1. **Abre tu terminal de MetaTrader 5** en Windows y asegúrate de que la cuenta esté conectada correctamente y tenga conexión a internet.
2. Ejecuta el archivo principal en la terminal donde activaste tu entorno virtual:
   ```powershell
   python main.py
   ```
3. Abre tu navegador web y entra a:
   [http://localhost:5000](http://localhost:5000)

---

## 📊 Panel de Control (Dashboard Web)

Desde la interfaz local web podrás ver y gestionar:
- **Balance, Equity y P&L**: Estado financiero del bot y de las operaciones abiertas.
- **Rendimiento**: Ratio de acierto (Win Rate) y pérdidas totales.
- **Registro de Decisiones**: Por qué la IA decidió comprar, vender o esperar en cada símbolo analizado.
- **Memoria y Aprendizaje**: Registro de lecciones que la IA escribe tras cerrar una operación para evitar cometer el mismo error en el futuro.
- **Acciones Rápidas**: Botón de pánico para **Cerrar todas las posiciones** activas y botón para **Pausar/Reanudar** el bot en caliente.
