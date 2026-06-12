"""UI web del bot: paginas + API que el frontend consulta cada 2 segundos.

Si UI_PASSWORD esta definida en el .env, TODO (paginas y API) exige login.
Es obligatorio cuando el panel se expone fuera de localhost (VPS).
"""
import hmac
import time
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session

import config
from app.state import state

app = Flask(__name__)
app.secret_key = config.ui_secret_key()
app.permanent_session_lifetime = timedelta(days=30)

# Se inyectan desde main.py
mt5_client = None
memory = None
bot = None
news_center = None


# ---------- Autenticacion ----------

def _auth_enabled() -> bool:
    return bool(config.UI_PASSWORD)


@app.context_processor
def _inject_auth():
    return {"auth_enabled": _auth_enabled()}


@app.before_request
def _require_login():
    if not _auth_enabled():
        return None
    if request.endpoint in ("page_login", "static") or session.get("auth"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "no autenticado"}), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def page_login():
    if not _auth_enabled() or session.get("auth"):
        return redirect("/")
    error = ""
    if request.method == "POST":
        time.sleep(1.2)  # frena ataques de fuerza bruta
        password = str(request.form.get("password") or "")
        if hmac.compare_digest(password, config.UI_PASSWORD):
            session.permanent = True
            session["auth"] = True
            return redirect("/")
        error = "Contraseña incorrecta"
        state.log("error", f"LOGIN UI fallido desde {request.remote_addr}")
    return render_template("login.html", error=error)


@app.route("/logout")
def page_logout():
    session.clear()
    return redirect("/login" if _auth_enabled() else "/")


# ---------- Paginas ----------

@app.route("/")
def page_dashboard():
    return render_template("dashboard.html", page="dashboard")


@app.route("/posiciones")
def page_positions():
    return render_template("positions.html", page="posiciones")


@app.route("/historial")
def page_history():
    return render_template("history.html", page="historial")


@app.route("/ia")
def page_ai():
    return render_template("ai.html", page="ia")


@app.route("/pensamientos")
def page_thoughts():
    return render_template("thoughts.html", page="pensamientos")


@app.route("/noticias")
def page_news():
    return render_template("news.html", page="noticias")


@app.route("/logs")
def page_logs():
    return render_template("logs.html", page="logs")


@app.route("/ajustes")
def page_settings():
    return render_template("settings.html", page="ajustes")


# ---------- API ----------

@app.route("/api/state")
def api_state():
    data = state.snapshot()
    data["stats"] = memory.stats()
    data["stats"]["calibration"] = memory.confidence_calibration()
    data["recent_trades"] = memory.recent_trades(20)
    data["lessons"] = memory.recent_lessons(15)
    data["rules"] = memory.get_rules()
    data["settings"] = config.runtime_settings()
    return jsonify(data)


@app.route("/api/thoughts")
def api_thoughts():
    """Registro completo de cada llamada a la IA (pagina Pensamientos)."""
    try:
        limit = max(1, min(200, int(request.args.get("limit", 60))))
    except (TypeError, ValueError):
        limit = 60
    return jsonify(state.thoughts_snapshot(
        limit=limit,
        symbol=request.args.get("symbol", "").strip(),
        phase=request.args.get("phase", "").strip(),
    ))


@app.route("/api/news")
def api_news():
    """Noticias en vivo + calendario + que influye en cada simbolo (pagina Noticias)."""
    if news_center is None or not config.NEWS_ENABLED:
        return jsonify({"enabled": False, "items": [], "calendar": [],
                        "influence": {}, "status": {}})
    try:
        limit = max(1, min(200, int(request.args.get("limit", 100))))
    except (TypeError, ValueError):
        limit = 100
    return jsonify({
        "enabled": True,
        "items": news_center.feed(limit=limit),
        "calendar": news_center.calendar_upcoming(48),
        "influence": news_center.influence(config.SYMBOLS),
        "status": news_center.status(),
    })


@app.route("/api/history")
def api_history():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except (TypeError, ValueError):
        limit = 200
    return jsonify(memory.recent_trades(limit))


@app.route("/api/settings", methods=["GET"])
def api_settings():
    return jsonify(config.runtime_settings())


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    data = request.get_json(silent=True) or {}
    try:
        applied = config.update_settings(data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    state.update(trading_enabled=config.TRADING_ENABLED)
    changes = ", ".join(f"{k}={v}" for k, v in applied.items())
    state.log("info", f"AJUSTES guardados desde la UI: {changes}")
    return jsonify({"ok": True, "settings": config.runtime_settings()})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    state.update(paused=True)
    state.log("info", "PAUSA desde la UI: no se abriran trades nuevos; "
                      "la IA sigue gestionando las posiciones abiertas")
    return jsonify({"ok": True})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    state.update(paused=False)
    state.log("info", "REANUDADO desde la UI: el bot vuelve a abrir trades nuevos")
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def api_login():
    """Login desde la UI: guarda credenciales en el .env y conecta a MT5."""
    data = request.get_json(silent=True) or {}
    try:
        login = int(str(data.get("login", "")).strip())
    except ValueError:
        return jsonify({"ok": False, "error": "el login debe ser el numero de cuenta"}), 400
    password = str(data.get("password", "")).strip()
    server_name = str(data.get("server", "")).strip()
    if not login or not password or not server_name:
        return jsonify({"ok": False, "error": "completa login, password y servidor"}), 400

    state.log("info", f"Iniciando sesion en la cuenta {login} @ {server_name}...")
    result = mt5_client.login_account(login, password, server_name)
    if result["ok"]:
        config.save_mt5_credentials(login, password, server_name)
        bot.reset_daily_baseline()
        info = result["account"]
        state.update(connected=True, account=info, positions=[], daily_loss_triggered=False)
        state.log("info", f"Sesion iniciada: {info['login']} @ {info['server']} | "
                          f"Balance: {info['balance']:.2f} {info['currency']} "
                          f"(credenciales guardadas en .env)")
        if result.get("warning"):
            state.log("error", f"AVISO: {result['warning']}")
    else:
        state.log("error", f"Fallo el login: {result['error']}")
    return jsonify(result)


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
    if not mt5_client.connected:
        return jsonify({"ok": False, "error": "sin sesion MT5"})

    closed, errors, messages = 0, 0, []
    with mt5_client.lock:  # que el bot no opere a mitad del cierre masivo
        positions = mt5_client.positions()
        if not positions:
            state.log("info", "CERRAR TODO: el bot no tiene posiciones abiertas")
            return jsonify({"ok": True, "closed": 0, "errors": 0,
                            "error": "el bot no tiene posiciones abiertas"})
        for p in positions:
            try:
                result = mt5_client.close_position(p["ticket"])
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            if result["ok"]:
                closed += 1
            else:
                errors += 1
                messages.append(f"#{p['ticket']} {p['symbol']}: {result['error']}")
                state.log("error", f"No se pudo cerrar #{p['ticket']}: {result['error']}")

    state.log("trade", f"CERRAR TODO desde la UI: {closed} cerradas, {errors} errores")
    return jsonify({"ok": errors == 0, "closed": closed, "errors": errors,
                    "error": "; ".join(messages)})


@app.route("/api/close_position", methods=["POST"])
def api_close_position():
    """Cierra una posicion concreta del bot desde la UI."""
    if not mt5_client.connected:
        return jsonify({"ok": False, "error": "sin sesion MT5"})
    try:
        ticket = int((request.get_json(silent=True) or {}).get("ticket", 0))
    except (TypeError, ValueError):
        ticket = 0
    if not ticket:
        return jsonify({"ok": False, "error": "ticket invalido"}), 400

    with mt5_client.lock:
        try:
            result = mt5_client.close_position(ticket)
        except Exception as e:
            result = {"ok": False, "error": str(e)}

    if result["ok"]:
        state.log("trade", f"POSICION #{ticket} cerrada desde la UI")
    else:
        state.log("error", f"No se pudo cerrar #{ticket}: {result['error']}")
    return jsonify(result)
