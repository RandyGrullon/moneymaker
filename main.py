"""Punto de entrada: conecta a MT5, arranca el bot en un thread y la UI web.

Uso:
    python main.py
Luego abre http://localhost:5000
"""
import sys
import threading

import config
from app.ai_brain import AIBrain
from app.memory import Memory
from app.mt5_client import MT5Client
from app.news import NewsCenter
from app.state import state
from app.trader import TradingBot
from ui import server


def main():
    if config.AI_PROVIDER == "ollama":
        print(f"IA local: Ollama @ {config.OLLAMA_URL} | modelo {config.OLLAMA_MODEL}")
    elif not config.GROQ_API_KEY:
        print("ERROR: falta GROQ_API_KEY en el archivo .env")
        print("Copia .env.example a .env y completa tus datos "
              "(o usa AI_PROVIDER=ollama para IA local).")
        sys.exit(1)

    # Seguridad: exponer el panel fuera de localhost (VPS) exige contraseña.
    if not config.ui_is_local() and not config.UI_PASSWORD:
        print(f"ERROR: UI_HOST={config.UI_HOST} expone el panel a internet SIN contraseña.")
        print("Cualquiera podria cerrar tus posiciones o cambiar tu cuenta.")
        print("Pon UI_PASSWORD=una_clave_fuerte en el .env (o vuelve a UI_HOST=127.0.0.1).")
        sys.exit(1)

    print("Conectando a MetaTrader 5...")
    mt5_client = MT5Client()
    try:
        mt5_client.connect()
        account = mt5_client.account()
        print(f"Conectado: cuenta {account['login']} @ {account['server']} | "
              f"Balance: {account['balance']:.2f} {account['currency']}")
    except Exception as e:
        print(f"AVISO: no se pudo conectar a MT5 todavia: {e}")
        print("La UI arrancara igualmente: inicia sesion desde el dashboard "
              "(se guardara en el .env).")
    if not config.TRADING_ENABLED:
        print("MODO ANALISIS: TRADING_ENABLED=false, no se enviaran ordenes reales.")

    memory = Memory()
    brain = AIBrain()
    news = NewsCenter()
    bot = TradingBot(mt5_client, brain, memory, news)

    if config.NEWS_ENABLED:
        news.start()
        print(f"Noticias en vivo: {news.status()['sources']} fuentes RSS + calendario economico")
    else:
        print("Noticias en vivo: desactivadas (NEWS_ENABLED=false)")

    # Inyectar dependencias en la UI
    server.mt5_client = mt5_client
    server.memory = memory
    server.bot = bot
    server.news_center = news

    # Bot en segundo plano, UI en el thread principal
    bot_thread = threading.Thread(target=bot.run_forever, daemon=True)
    bot_thread.start()

    print(f"\nDashboard: http://localhost:{config.UI_PORT}")
    if not config.ui_is_local():
        print(f"Acceso remoto: http://IP_DE_ESTA_MAQUINA:{config.UI_PORT} "
              f"(protegido con UI_PASSWORD; abre el puerto en el firewall)")
    print()

    try:
        # Servidor de produccion (local y VPS): estable y multi-hilo
        from waitress import serve
        serve(server.app, host=config.UI_HOST, port=config.UI_PORT, threads=8)
    except ImportError:
        print("AVISO: 'waitress' no esta instalado (pip install -r requirements.txt).")
        print("Usando el servidor de desarrollo de Flask mientras tanto.")
        server.app.run(host=config.UI_HOST, port=config.UI_PORT, debug=False)


if __name__ == "__main__":
    main()
