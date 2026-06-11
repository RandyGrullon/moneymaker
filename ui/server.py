"""UI web del bot: dashboard + API que la pagina consulta cada 2 segundos."""
from flask import Flask, jsonify, render_template, request

import config
from app.state import state

app = Flask(__name__)

# Se inyectan desde main.py
mt5_client = None
memory = None
bot = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    data = state.snapshot()
    data["stats"] = memory.stats()
    data["recent_trades"] = memory.recent_trades(20)
    data["lessons"] = memory.recent_lessons(15)
    return jsonify(data)


@app.route("/api/pause", methods=["POST"])
def api_pause():
    state.update(paused=True)
    state.log("info", "Bot PAUSADO desde la UI")
    return jsonify({"ok": True})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    state.update(paused=False)
    state.log("info", "Bot REANUDADO desde la UI")
    return jsonify({"ok": True})


@app.route("/api/accounts")
def api_accounts():
    """Lista de cuentas disponibles (sin exponer las contrasenas)."""
    current = state.snapshot()["account"].get("login")
    return jsonify([
        {"name": a["name"], "login": a["login"], "server": a.get("server", ""),
         "current": a["login"] == current}
        for a in config.load_accounts()
    ])


@app.route("/api/switch_account", methods=["POST"])
def api_switch_account():
    name = (request.get_json(silent=True) or {}).get("name", "")
    account = next((a for a in config.load_accounts() if a["name"] == name), None)
    if account is None:
        return jsonify({"ok": False, "error": "cuenta no encontrada"}), 404

    state.log("info", f"Cambiando a la cuenta '{name}'...")
    result = mt5_client.switch_account(
        account["login"], account.get("password", ""), account.get("server", "")
    )
    if result["ok"]:
        bot.reset_daily_baseline()
        info = result["account"]
        state.update(account=info, positions=[], daily_loss_triggered=False)
        state.log("info", f"Cuenta activa: {info['login']} @ {info['server']} | "
                          f"Balance: {info['balance']:.2f} {info['currency']}")
        if result.get("warning"):
            state.log("error", f"AVISO: {result['warning']}")
    else:
        state.log("error", f"Fallo el cambio de cuenta: {result['error']}")
    return jsonify(result)


@app.route("/api/close_all", methods=["POST"])
def api_close_all():
    closed, errors = 0, 0
    for p in mt5_client.positions():
        result = mt5_client.close_position(p["ticket"])
        if result["ok"]:
            closed += 1
        else:
            errors += 1
            state.log("error", f"No se pudo cerrar #{p['ticket']}: {result['error']}")
    state.log("trade", f"CERRAR TODO desde la UI: {closed} cerradas, {errors} errores")
    return jsonify({"ok": errors == 0, "closed": closed, "errors": errors})
