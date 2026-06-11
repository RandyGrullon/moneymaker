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
from app.state import state
from app.trader import TradingBot
from ui import server


def main():
    if not config.GROQ_API_KEY:
        print("ERROR: falta GROQ_API_KEY en el archivo .env")
        print("Copia .env.example a .env y completa tus datos.")
        sys.exit(1)

    print("Conectando a MetaTrader 5...")
    mt5_client = MT5Client()
    try:
        mt5_client.connect()
    except Exception as e:
        print(f"ERROR conectando a MT5: {e}")
        print("Verifica que el terminal MT5 este instalado y la cuenta logueada,")
        print("o completa MT5_LOGIN / MT5_PASSWORD / MT5_SERVER en el .env")
        sys.exit(1)

    account = mt5_client.account()
    print(f"Conectado: cuenta {account['login']} @ {account['server']} | "
          f"Balance: {account['balance']:.2f} {account['currency']}")
    if not config.TRADING_ENABLED:
        print("MODO ANALISIS: TRADING_ENABLED=false, no se enviaran ordenes reales.")

    memory = Memory()
    brain = AIBrain()
    bot = TradingBot(mt5_client, brain, memory)

    # Inyectar dependencias en la UI
    server.mt5_client = mt5_client
    server.memory = memory
    server.bot = bot

    # Bot en segundo plano, UI en el thread principal
    bot_thread = threading.Thread(target=bot.run_forever, daemon=True)
    bot_thread.start()

    print(f"\nDashboard: http://localhost:{config.UI_PORT}\n")
    server.app.run(host="127.0.0.1", port=config.UI_PORT, debug=False)


if __name__ == "__main__":
    main()
