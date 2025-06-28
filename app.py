import sys, os, subprocess, json, glob
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import threading
import webview
import hashlib
import requests
import shutil
import atexit
import time

# ─── Crypto imports ───────────────────────────────────────────────────────────
import base64
from datetime import datetime
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


log_file = open("info.log", "w", encoding="utf-8", buffering=1)
sys.stdout = log_file
sys.stderr = log_file
log_file.write(f"\n\n--- EJECUCIÓN {datetime.now().isoformat()} ---\n")

# ─── Determina la raíz de la app (dev vs bundled .exe) ────────────────
if getattr(sys, "frozen", False):
    # Ejecutado desde un .exe (modo PyInstaller)
    BASE_PATH = sys._MEIPASS  # Para archivos empaquetados
    EXE_DIR = os.path.dirname(sys.executable)  # Para archivos externos (como license.key o public_key.pem)
else:
    # Modo desarrollo
    BASE_PATH = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = BASE_PATH

# Proceso global
bot_process = None
# ─── Rutas de ficheros y carpetas ────────────────────────────────────
# Archivos externos (NO incluidos con --add-data)
LICENSE_PATH = os.path.join(EXE_DIR, "license.key")
PUB_PATH     = os.path.join(EXE_DIR, "public_key.pem")

# Archivos empaquetados (sí incluidos con --add-data)
BOT_DIR         = os.path.join(BASE_PATH, "bot")
MESSAGES_PATH   = os.path.join(BOT_DIR, "mensajes.xlsx")
LOG_PATH        = os.path.join(BOT_DIR, "log_publicaciones.txt")
LOG_PATH_TEMPLATE = os.path.join(BOT_DIR, "log_%s.txt")
if getattr(sys, "frozen", False):
    ACCOUNTS_PATH = os.path.join(EXE_DIR, "accounts.json")
else:
    ACCOUNTS_PATH = os.path.join(BASE_PATH, "accounts.json")
COUNTDOWN_FILE  = os.path.join(BOT_DIR, "countdown.json")

LOGS_DOWNLOAD_DIR = os.path.join(EXE_DIR, "log")
os.makedirs(LOGS_DOWNLOAD_DIR, exist_ok=True)

sys.path.insert(0, BOT_DIR)

logs_dir = BOT_DIR 
pattern = os.path.join(logs_dir, "log*.txt")

python_path_file = os.path.join(EXE_DIR, "python_path.txt")
with open(python_path_file, "r", encoding="utf-8") as f:
    real_python = f.read().strip()


# ─── Tu clave pública (en PEM) ────────────────────────────────────────────────
with open(PUB_PATH, "rb") as f:
    public_key = serialization.load_pem_public_key(f.read())


# Flask setup
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_PATH, "templates"),
    static_folder=os.path.join(BASE_PATH, "static"),
)

app.secret_key = os.environ.get("SECRET_KEY", "TumadreYmiMadreSelaPasanBrutal")

# ► Limpia cualquier fichero de countdown sobrante al arrancar
if os.path.exists(COUNTDOWN_FILE):
    os.remove(COUNTDOWN_FILE)


@app.route("/", methods=["GET", "POST"])
def index():
    global bot_process
    # Manejo de subida de Excel
    if request.method == "POST" and "archivo_excel" in request.files:
        f = request.files["archivo_excel"]
        if f and f.filename.endswith(".xlsx"):
            f.save(MESSAGES_PATH)
        return redirect(url_for("index"))

    # Comprueba si el bot está corriendo
    running = bot_process is not None and bot_process.poll() is None

    # 1) Carga logs por dispositivo y extrae la última línea como acción
    device_logs = {}
    device_statuses = {}
    for path in glob.glob(os.path.join(BOT_DIR, "log_*.txt")):
        udid = os.path.splitext(os.path.basename(path))[0].split("_", 1)[1]
        try:
            with open(path, "r", encoding="utf-8") as lf:
                content = lf.read()
            device_logs[udid] = content
            lines = [l for l in content.strip().splitlines() if l]
            device_statuses[udid] = lines[-1] if lines else ""
        except FileNotFoundError:
            device_logs[udid] = ""
            device_statuses[udid] = ""

    # 2) Además, leemos accounts.json para conocer los UDIDs que el bot escaneó al arrancar
    try:
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            cuentas = json.load(f)
        for c in cuentas:
            ud = c.get("device")
            if ud and ud not in device_statuses and ud != "publicaciones":
                device_statuses[ud] = "⏳ Esperando primera actividad..."
                device_logs[ud] = ""
    except Exception:
        pass

    # 3) (Opcional) Mantenemos también tu lógica de countdown_<udid>.json
    #    (por si quieres seguir usándola)
    for fpath in glob.glob(os.path.join(BOT_DIR, "countdown_*.json")):
        nombre = os.path.splitext(os.path.basename(fpath))[0]
        parts = nombre.split("_", 1)
        if len(parts) == 2:
            ud = parts[1]
            if ud not in device_statuses and ud != "publicaciones":
                device_statuses[ud] = "⏳ Esperando primera actividad..."
                device_logs[ud] = ""

    # Log general (compatibilidad)
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as lf:
            log_content = lf.read()
    except FileNotFoundError:
        log_content = ""

    archivo_subido = os.path.exists(MESSAGES_PATH)

    # 4) Filtramos “publicaciones” de los dispositivos normales
    other_devices = {
        ud: st for ud, st in device_statuses.items()
        if ud != "publicaciones"
    }

    return render_template(
        "index.html",
        bot_running     = running,
        log_content     = log_content,
        device_logs     = device_logs,
        device_statuses = device_statuses,
        archivo_subido  = archivo_subido,
        publicaciones   = device_statuses.get("publicaciones", ""),
        other_devices   = other_devices
    )


@app.route("/lanzar_bot", methods=["POST"])
def lanzar_bot():
    global bot_process

    usb_flag = request.form.get("usb")
    social_flag = request.form.get("social")  # viene "1" si checked
    wait_min = int(request.form.get("wait_min", "10"))
    wait_sec = int(request.form.get("wait_sec", "0"))

    if bot_process is None or bot_process.poll() is not None:
        '''if os.path.exists(LOG_PATH):
            os.remove(LOG_PATH)'''
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                log_file.write(f"✅ Eliminado: {os.path.basename(filepath)}")
            except OSError as e:
                log_file.write(f"❌ No se pudo eliminar {os.path.basename(filepath)}: {e}")

        cmd = [real_python, os.path.join(BOT_DIR, "bot.py")]

        if usb_flag == "1":
            cmd.append("--usb")
        if social_flag == "1":
            cmd.append("--social")

        # Convertir tiempo a segundos
        total_wait = wait_min * 60 + wait_sec
        cmd.extend(["--wait", str(total_wait)])

        bot_process = subprocess.Popen(
            cmd,
            cwd=BOT_DIR,
            env=os.environ,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )

    return redirect(url_for("index"))

@app.route("/detener_bot", methods=["POST"])
def detener_bot():
    global bot_process

    try:
        if bot_process and bot_process.poll() is None:
            log_file.write("🛑 Deteniendo proceso del bot...")
            bot_process.terminate()
            try:
                bot_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log_file.write("⏱ Timeout al detener, forzando kill()")
                bot_process.kill()
        else:
            log_file.write("ℹ️ No hay bot corriendo o ya fue detenido.")
    except Exception as e:
        log_file.write(f"❌ Error al intentar detener el bot: {e}")

    # Borrar cuenta atrás
    if os.path.exists(COUNTDOWN_FILE):
        try:
            os.remove(COUNTDOWN_FILE)
            log_file.write("✅ countdown.json eliminado correctamente")
        except Exception as e:
            log_file.write(f"⚠️ Error al borrar countdown.json: {e}")

    return redirect(url_for("index"))



def _windows_machine_guid():
    import winreg
    # Abrimos la rama de registro 64-bit por si estamos en un Windows a 64 bits
    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    )
    guid, _ = winreg.QueryValueEx(key, "MachineGuid")
    return guid

def _linux_machine_id():
    # En la mayoría de distros Linux y en macOS (/etc/machine-id)
    path = "/etc/machine-id"
    if not os.path.exists(path):
        # En macOS puede ser distinto, prueba también /var/db/uuid
        path = "/var/db/uuid"
    with open(path, "r") as f:
        return f.read().strip()

def get_machine_id():
    if sys.platform.startswith("win"):
        base_id = _windows_machine_guid()
    else:
        base_id = _linux_machine_id()

    # Opcional: añadir hostname si quieres mezclar datos
    import platform
    host = platform.node()

    raw = f"{base_id}-{host}"
    return hashlib.sha256(raw.encode()).hexdigest()



def validar_machine_id_remoto(machine_id: str) -> bool:
    try:
        r = requests.post(
            "https://servidor-licencias-production.up.railway.app/verificar",
            json={"machine_id": machine_id},
            timeout=5
        )
        r.raise_for_status()
        return r.json().get("autorizado", False)
    except Exception as e:
        log_file.write(f"⚠️ Error al verificar machine_id en el servidor")
        return False

@app.route("/leer_cuentas", methods=["POST"])
def leer_cuentas():
    global bot_process
    # No permitir leer cuentas si el bot está vivo
    if bot_process and bot_process.poll() is None:
        flash("🚫 Detén primero el bot para leer cuentas", "warning")
        return redirect(url_for("index"))

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = BASE_PATH

    # construimos el comando incluyendo --out-dir
    bot_script = os.path.join(BOT_DIR, "bot.py")
    cmd = [
        real_python,
        bot_script,
        "--leer-cuentas",
        "--out-dir", exe_dir
    ]

    # lanzamos como antes
    proc = subprocess.run(
        cmd,
        cwd=BOT_DIR,
        capture_output=True,
        text=True,
        timeout=120
    )

    return redirect(url_for("index"))


class API:
    def guardar_log(self, udid):
        import shutil
        path_origen = LOG_PATH_TEMPLATE % udid
        destino_dir = os.path.join(EXE_DIR, "log")
        os.makedirs(destino_dir, exist_ok=True)
        destino = os.path.join(destino_dir, f"log_{udid}.txt")

        if not os.path.exists(path_origen):
            return {"status": "error", "message": "Archivo no encontrado"}

        try:
            shutil.copy2(path_origen, destino)
            return {"status": "ok", "destino": destino}
        except Exception as e:
            return {"status": "error", "message": str(e)}

@app.route("/log/")
@app.route("/log/<udid>")
def log_route(udid=""):
    if udid:
        path = LOG_PATH_TEMPLATE % udid
    else:
        path = LOG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    return jsonify({"log": content})

@app.route("/last_action/<udid>")
def last_action(udid):
    path = LOG_PATH_TEMPLATE % udid
    last = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line.strip()
    except FileNotFoundError:
        pass
    return jsonify({"action": last})

@app.route("/download_log/<udid>")
def download_log(udid):
    path_origen = LOG_PATH_TEMPLATE % udid
    if not os.path.exists(path_origen):
        return "", 404

    destino_dir = os.path.join(EXE_DIR, "log")
    os.makedirs(destino_dir, exist_ok=True)
    destino = os.path.join(destino_dir, f"log_{udid}.txt")
    try:
        shutil.copy2(path_origen, destino)
        return jsonify({"status": "ok", "destino": destino})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route("/status")
def status():
    running = bot_process is not None and bot_process.poll() is None
    return jsonify({"running": running})

@app.route("/status/<udid>")
def status_udid(udid):
    path = LOG_PATH_TEMPLATE % udid
    try:
        with open(path, "r", encoding="utf-8") as f:
            last_line = ""
            for line in f:
                if line.strip():
                    last_line = line.strip()
            # Consideramos "activo" si la última línea NO incluye "⛔" o "Finalizado"
            activo = not any(x in last_line.lower() for x in ["⛔", "finalizado", "cerrado", "error"])
            return jsonify({"running": activo})
    except FileNotFoundError:
        return jsonify({"running": False})



@app.route("/countdown")
def countdown():
    path = os.path.join(BOT_DIR, "countdown.json")
    running = bot_process is not None and bot_process.poll() is None

    if not running:
        # Si no hay bot, nos aseguramos de borrar el fichero y devolvemos 0
        if os.path.exists(path):
            os.remove(path)
        return jsonify({"remaining": 0, "mins": 0, "secs": 0, "total": 1})  # total=1 evita división por 0

    # Si está corriendo, devolvemos lo que haya en el fichero
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Aseguramos que el campo "total" esté presente (por compatibilidad)
            if "total" not in data:
                data["total"] = data.get("remaining", 1)
    except Exception:
        data = {"remaining": 0, "mins": 0, "secs": 0, "total": 1}
    return jsonify(data)

@app.route("/countdown/<udid>")
def countdown_udid(udid):
    path = os.path.join(BOT_DIR, f"countdown_{udid}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"mins": 0, "secs": 0, "remaining": 0, "total": 1})


def _b64url_decode_nopad(data_str: str) -> bytes:
    """
    Añade los '=' que falten para que la longitud sea múltiplo de 4,
    y luego decodifica Base64-url.
    """
    padding_needed = -len(data_str) % 4
    return base64.urlsafe_b64decode(data_str + ("=" * padding_needed))


def validate_license(token: str) -> dict:
    """
    Verifica que el token (data_b64.sig_b64) esté firmado con tu clave privada
    y devuelve el payload JSON si es válido.
    """
    try:
        data_b64, sig_b64 = token.strip().split(".")
        raw = _b64url_decode_nopad(data_b64)
        sig = _b64url_decode_nopad(sig_b64)

        # Verificación con PKCS#1 v1.5 + SHA256 (igual que emite_licencia.py)
        public_key.verify(
            sig,
            raw,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        payload = json.loads(raw.decode("utf-8"))

        # Comprueba caducidad
        if "expires" in payload:
            exp = datetime.fromisoformat(payload["expires"])
            if datetime.now() > exp:
                raise ValueError("Licencia expirada")

        return payload

    except Exception as e:
        raise ValueError(f"Licencia inválida: {e}")

def iniciar_flask():
    from app import app  # o tu objeto Flask si lo tienes separado
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)


'''def _remove_readonly(func, path, excinfo):
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except PermissionError:
        pass  # silenciar WinError 5
    except Exception as e:
        log_file.write(f"❌ Error al forzar permiso en {path}: {e}\n")'''
def _remove_readonly(func, path, excinfo):
    try:
        func(path)
    except Exception:
        pass



if getattr(sys, 'frozen', False):
    def delayed_cleanup_temp_dir():
        time.sleep(5)  # espera para que todos los hilos se cierren
        try:
            shutil.rmtree(sys._MEIPASS, onerror=_remove_readonly)
            log_file.write("✅ _MEIPASS eliminado correctamente\n")
        except Exception as e:
            log_file.write(f"⚠️ No se pudo borrar _MEIPASS: {e}\n")
    atexit.register(delayed_cleanup_temp_dir)


if __name__ == "__main__":
    # ─── 1) Carga el token desde license.key ─────────────────────────────────
    if not os.path.exists(LICENSE_PATH):
        log_file.write("❌ No se encontró license.key junto al .exe")
        sys.exit(1)

    raw_token = open(LICENSE_PATH, "r", encoding="utf-8").read().strip()
    try:
        lic_data = validate_license(raw_token)
        log_file.write(f"✅ Licencia válida: {lic_data}" )

         # Verificación remota de machine_id
        machine_id = get_machine_id()
        if not validar_machine_id_remoto(machine_id):
            log_file.write("❌ Este dispositivo no está autorizado para usar esta licencia.")
            sys.exit(1)
    except ValueError as exc:
        log_file.write(f"❌ {exc}")
        sys.exit(1)

    # Si no existe el archivo, créalo vacío
    if not os.path.exists(ACCOUNTS_PATH):
        with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)


    # ─── 2) Si llegamos aquí, la licencia es buena → arrancamos la app ────────
    flask_thread = threading.Thread(target=iniciar_flask)
    flask_thread.daemon = True
    flask_thread.start()

    window=webview.create_window(
        "Bot Threads",
        "http://127.0.0.1:5000",
        frameless=False,
        width=1400, height=1100,
        js_api=API(),
        confirm_close=False,
        background_color='#121212'
    )
    
    webview.start( gui='edgechromium' if sys.platform == 'win32' else None)


