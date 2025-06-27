import subprocess
import time
import socket
import os
import sys
from datetime import datetime
import openpyxl
import uiautomator2 as u2
import json
import random
import threading
import shutil
import re
import traceback

# Flag para usar dispositivo real vía USB
use_usb = False
if "--usb" in sys.argv:
    use_usb = True
    sys.argv.remove("--usb")

use_social = False
if "--social" in sys.argv:
    use_social = True
    sys.argv.remove("--social")


# Rutas base
if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
    SCRIPT_DIR = os.path.join(sys._MEIPASS, "bot")
    ACCOUNTS_PATH = os.path.join(EXE_DIR, "accounts.json")
    BASE_DIR  = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
    SCRIPT_DIR = BASE_DIR
    ACCOUNTS_PATH = os.path.join(BASE_DIR, "..", "accounts.json")

RUTA_FOTOS       = os.path.join(os.path.dirname(BASE_DIR), "media", "fotos")
LAUNCHER_SCRIPT  = os.path.join(SCRIPT_DIR, "liberar_puerto.py")

LOG_PATH_TEMPLATE       = os.path.join(BASE_DIR, "log_%s.txt")
COUNTDOWN_FILE_TEMPLATE = os.path.join(BASE_DIR, "countdown_%s.json")
DEFAULT_LOG_PATH        = os.path.join(BASE_DIR, "log_publicaciones.txt")
DEFAULT_COUNTDOWN_FILE  = os.path.join(BASE_DIR, "countdown.json")
REMOTE_PHOTO_DIR        = os.environ.get("THREADS_PHOTO_DIR", "Pictures/threads-bot")

# Reemplazar caracteres problemáticos para usar el UDID como parte de un nombre de archivo
def safe_udid(udid: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in udid)

device_locks = {}
thread_local = threading.local()

def get_log_path():
    return getattr(thread_local, "log_path", DEFAULT_LOG_PATH)

def get_countdown_file():
    return getattr(thread_local, "countdown_file", DEFAULT_COUNTDOWN_FILE)

def log(msg):
    ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(get_log_path(), "a", encoding="utf-8") as f:
        f.write(f"{ts} {msg}\n")

def log_info(msg):  log(f"ℹ️ INFO: {msg}")
def log_ok(msg):    log(f"✅ OK: {msg}")
def log_warn(msg):  log(f"⚠️ WARN: {msg}")
def log_error(msg): log(f"❌ ERROR: {msg}")

def connect_device(serial: str, timeout: float = 20.0):
    """Conecta por USB o TCP/IP según el serial."""
    log_info(f"Conectando a dispositivo {serial}…")
    if ":" in serial:
        d = u2.connect(serial)
        log_ok(f"Conectado vía TCP/IP a {serial}")
    else:
        d = u2.connect_usb(serial)
        log_ok(f"Conectado vía USB a {serial}")
    d.wait_timeout = timeout
    return d

def cargar_imagenes():
    """
    Devuelve una lista de rutas absolutas a las imágenes dentro de media/fotos
    """
    fotos_dir = RUTA_FOTOS
    if not os.path.exists(fotos_dir):
        log("⚠️ No se encontró el directorio media/fotos, se creará vacío")
        os.makedirs(fotos_dir, exist_ok=True)

    extensiones_validas = (".jpg", ".jpeg", ".png", ".webp")
    imagenes = [
        os.path.join(fotos_dir, f)
        for f in os.listdir(fotos_dir)
        if f.lower().endswith(extensiones_validas)
    ]
    return imagenes

def long_press_shell(udid, x, y, duration_ms):
    # inyecta un gesto de pulsación larga usando adb shell input swipe
    cmd = [
        "adb", "-s", udid,
        "shell", "input", "swipe",
        str(x), str(y), str(x), str(y), str(duration_ms)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Borrar carpeta del móvil si existe (evita residuos de ejecuciones anteriores)
def borrar_carpeta_movil(udid, carpeta):
    resultado = subprocess.run(
        ["adb", "-s", udid, "shell", "rm", "-r", f"/sdcard/{carpeta}"],
        capture_output=True, text=True
    )
    if resultado.returncode == 0:
        log(f"[{udid}] 🧹 Carpeta {carpeta} (movil) eliminada al iniciar")
    else:
        log(f"[{udid}] ⚠️ No se pudo borrar carpeta remota: {resultado.stderr.strip()}")

def dividir_cuentas(lista, n):
    k, m = divmod(len(lista), n)
    return [lista[i*k + min(i, m):(i+1)*k + min(i+1, m)] for i in range(n)]

def cargar_entradas_con_tipo(ruta_excel):
    wb = openpyxl.load_workbook(ruta_excel)
    hoja = wb.active
    entradas = []
    for fila in hoja.iter_rows(min_row=2, values_only=True):
        if len(fila) <= 5 or not fila[5]:
            break
        tipo     = str(fila[5]).strip().lower()
        mensaje  = str(fila[0]).strip() if fila[0] else None
        foto     = int(fila[1])         if len(fila)>1 and fila[1] else None
        pregunta = str(fila[2]).strip() if len(fila)>2 and fila[2] else None
        opcion1  = str(fila[3]).strip() if len(fila)>3 and fila[3] else None
        opcion2  = str(fila[4]).strip() if len(fila)>4 and fila[4] else None
        entradas.append({
            "tipo": tipo,
            "mensaje": mensaje,
            "foto": foto,
            "pregunta": pregunta,
            "opcion1": opcion1,
            "opcion2": opcion2
        })
    return entradas

def obtener_dispositivos_usb(timeout=5):
    log_info("🔌 Buscando dispositivos USB conectados…")
    inicio = time.time()
    while time.time() - inicio < timeout:
        salida = subprocess.getoutput("adb devices")
        log_info(f"📥 Salida adb devices:\n{salida}")
        líneas = salida.splitlines()[1:]  # Ignora cabecera

        reales = [l.split()[0] for l in líneas if len(l.split()) >= 2 and l.strip().endswith("device") and not l.startswith("emulator-")]

        if reales:
            log_ok(f"✅ Dispositivos USB detectados: {reales}")
            return reales

        time.sleep(0.5)

    log_warn("❌ No hay dispositivos USB detectados")
    return []


def get_external_storage(device):
    out = subprocess.getoutput(f"adb -s {device} shell echo $EXTERNAL_STORAGE").strip()
    return out or "/sdcard"

def copiar_imagen_especifica(udid, ruta_origen, foto_num):
    """
    Sube solo la imagen número foto_num (1-based) de ruta_origen al dispositivo udid,
    en EXTERNAL_STORAGE/REMOTE_PHOTO_DIR. Antes, vacía esa carpeta para que solo haya una miniatura.
    """
    # 1. Listar y ordenar imágenes válidas
    try:
        archivos = sorted(
            f for f in os.listdir(ruta_origen)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )
    except FileNotFoundError:
        log_error(f"[{udid}] Carpeta de origen no encontrada: {ruta_origen}")
        return False

    if foto_num < 1 or foto_num > len(archivos):
        log_warn(f"[{udid}] Imagen #{foto_num} fuera de rango (hay {len(archivos)})")
        return False

    nombre = archivos[foto_num - 1]
    ruta_local = os.path.join(ruta_origen, nombre)

    # 2. Calcular ruta remota
    ext = get_external_storage(udid)
    remote_root = f"{ext}/{REMOTE_PHOTO_DIR}"

    # 3. Limpiar y crear carpeta remota
    subprocess.run(
        ["adb", "-s", udid, "shell", "rm", "-rf", remote_root],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["adb", "-s", udid, "shell", "mkdir", "-p", remote_root],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    log_info(f"[{udid}] Carpeta remota preparada: {remote_root}")

    # 4. Push de la imagen
    destino = f"{remote_root}/{nombre}"
    res = subprocess.run(
        ["adb", "-s", udid, "push", ruta_local, destino],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        log_error(f"[{udid}] Error al copiar {nombre}: {res.stderr.strip()}")
        return False
    log_ok(f"[{udid}] Copiada imagen específica: {nombre}")

    # 5. Forzar escaneo multimedia
    subprocess.run([
        "adb", "-s", udid, "shell", "am", "broadcast",
        "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
        "-d", f"file://{destino}"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log_ok(f"[{udid}] Escaneo multimedia solicitado para {nombre}")

    return True

def registrar_cuentas_dispositivo_u2(d, udid):
    log_info(f"[{udid}] ⏳ Intentando registrar cuentas…")

    ruta_account = ACCOUNTS_PATH
    # Solo a–z, 0–9, _ y . (3–30 chars)
    regex_usuario = re.compile(r"^[a-z0-9._]{3,30}$")

    # 🔼 Asegurar que la barra inferior sea visible
    d.swipe(360, 400, 360, 1400, steps=10)  # swipe hacia arriba para mostrar barra
    time.sleep(2)

    # 1. Intenta por content-desc "Profile"
    if d(description="Profile").exists:
        sel = d(description="Profile")

    # 2. Si no, intenta por resource-id corto
    elif d(resourceId="barcelona_tab_profile").exists:
        sel = d(resourceId="barcelona_tab_profile")

    # 3. Si no, intenta por resource-id completo
    elif d(resourceId="com.instagram.barcelona:id/barcelona_tab_profile").exists:
        sel = d(resourceId="com.instagram.barcelona:id/barcelona_tab_profile")

    # 4. Si no, intenta por descripción en español (por si el sistema está en español)
    elif d(description="Perfil").exists:
        sel = d(description="Perfil")

    # 4) Ahora esperamos y abortamos si no lo encontramos
    if not sel.wait(timeout=10):
        log_error(f"[{udid}] botón de perfil no encontrado; abortando.")
        raise RuntimeError  # o raise RuntimeError, según tu flujo

    # 5) Ya podemos hacer el long-press con seguridad
    b = sel.info["bounds"]
    x = (b["left"] + b["right"]) // 2
    y = (b["top"]  + b["bottom"]) // 2
    long_press_shell(udid, x, y, int(1.5*1000))
    time.sleep(3)

    # 3) Recolecta únicamente el primer TextView de cada bloque
    usuarios = []
    for bloque in d(className="android.view.View"):
        try:
            # .child() te devuelve el primer hijo que cumpla el selector
            tv = bloque.child(className="android.widget.TextView")
            txt = (tv.get_text() or "").strip()
            if regex_usuario.fullmatch(txt):
                usuarios.append(txt)
        except Exception:
            continue

    usuarios = list(dict.fromkeys(usuarios))

    # 4) Cierra el menú
    d.press("back")
    time.sleep(0.3)

    """
    # 5) Guarda solo esos usuarios
    cuentas = [
        {"usuario": u, "correo": "", "contrasena": "", "device": udid}
        for u in usuarios
    ]
    with open(ruta_account, "w", encoding="utf-8") as f:
        json.dump(cuentas, f, indent=2, ensure_ascii=False)"""

    log_ok(f"[{udid}] Cuentas registradas: {', '.join(usuarios)}")
    return usuarios

def esperar_tiempo(segundos_totales):
    for i in range(segundos_totales, 0, -1):
        mins, secs = divmod(i, 60)
        # Guardar countdown en JSON
        try:
            with open(get_countdown_file(), "w", encoding="utf-8") as f:
                json.dump({"remaining": i, "mins": mins, "secs": secs, "total": segundos_totales}, f)
        except Exception:
            pass
        time.sleep(1)
    try:
        os.remove(get_countdown_file())
    except Exception:
        pass

def revisar_posted(udid, d):
    """
    Revisa si la publicación se ha completado, usando uiautomator2 en lugar de Appium.
    - udid: ID del dispositivo (para logs)
    - d: objeto u2.Device
    """
    # 1️⃣ Intentar detectar el texto "Posted"
    if d(text="Posted").wait(timeout=30):
        log(f"[{udid}] 🆗 Confirmado con texto 'Posted'")
        return

    # 2️⃣ Esperar a que desaparezca el botón 'Post'
    #    el botón tiene resource-id new_thread_screen_post_button
    try:
        # esperamos a que deje de existir
        start = time.time()
        while time.time() - start < 30:
            if not d(resourceId="new_thread_screen_post_button").exists:
                log(f"[{udid}] 🆗 Confirmado con desaparición del botón 'Post'")
                return
            time.sleep(1)
    except Exception:
        pass

    # 3️⃣ Esperar a que vuelva al botón de perfil (pantalla principal)
    if d(resourceId="com.instagram.barcelona:id/barcelona_tab_profile").wait(timeout=30):
        log(f"[{udid}] 🆗 Confirmado con regreso a pantalla principal")
        return

    # Si llegamos aquí, no pudimos confirmar
    log(f"[{udid}] ⚠️ No se pudo confirmar visualmente que se publicó")

def abrir_app_threads(device):
    resultado = subprocess.getoutput(f"adb -s {device} shell monkey -p com.instagram.barcelona -c android.intent.category.LAUNCHER 1")
    if "monkey aborted" not in resultado:
        log_ok(f"[{device}] Threads lanzado")
    else:
        log_error(f"[{device}] Error lanzando Threads")

def cargar_mensajes_texto(ruta_excel):
    todas = cargar_entradas_con_tipo(ruta_excel)
    return [e["mensaje"] for e in todas if e["tipo"] == "mensaje" and e["mensaje"]]

def tap_with_jitter(device, info):
    # info = btn.info["bounds"]
    l, r = info["left"], info["right"]
    t, b = info["top"],  info["bottom"]
    # calcula un x dentro de [l+5, r-5]
    a, z = l+5, r-5
    if a > z:
        x = (l + r) // 2
    else:
        x = random.randint(a, z)
    # calcula un y dentro de [t+5, b-5]
    c, d = t+5, b-5
    if c > d:
        y = (t + b) // 2
    else:
        y = random.randint(c, d)
    device.shell(f"input tap {x} {y}")

def revisar_threads_primer_plano(device):
    curr = device.app_current().get("packageName", "")
    if curr != "com.instagram.barcelona":
        device.app_start("com.instagram.barcelona")
        time.sleep(4)

def detectar_cuenta_actual(device):
    actual_xpath = "//android.view.View[@clickable='true'][.//android.widget.TextView[@content-desc='Current account']]"
    actual_node = device.xpath(actual_xpath)
    if actual_node.exists:
        return actual_node.xpath(".//android.widget.TextView").get_text()
    return None


def esperar_tiempo_social_humano(segundos_totales, device: u2.Device,
                                  mensajes_texto, cuentas: list[dict],
                                  like_prob,
                                  max_likes_por_cuenta):
    fin = time.time() + segundos_totales

    # Lista de comentarios posibles
    comentarios = [
        "genial", "👀", "que interesante", "🙌", "😮", "👍", "❤️", "que bonito!", "que gran contenido"
    ]

    if len(cuentas) <= 1:
        log_warn("⚠️ Solo hay una cuenta, no se realizará rotación social.")
        return
    
    cuentas_usadas = set()

    while time.time() < fin:
        # Detectar cuenta actual (la que tiene content-desc="Current account")
        # Scroll para asegurar barra
        x = random.randint(300, 400)
        y1, y2 = 500, 1400
        device.swipe(x, y1, x + random.randint(-20, 20), y2 + random.randint(-20, 20),
                     steps=random.randint(5, 12))
        time.sleep(random.uniform(3.3, 5.4))        

        # 1. Intentar localizar el botón "Feed"
        btn_feed = (device(description="Feed") or device(resourceId="barcelona_tab_main_feed"))

        # 2. Si existe, hacer clic para subir al inicio del feed
        if btn_feed.wait(timeout=5):
            b = btn_feed.info["bounds"]
            cx = (b["left"] + b["right"]) // 2
            cy = (b["top"] + b["bottom"]) // 2
            device.click(cx, cy)
            log_ok("🔝 Pulsado el botón 'Feed' para subir al inicio.")
            time.sleep(random.uniform(1.0, 2.0))
        else:
            log_warn("⚠️ No se encontró el botón 'Feed'")

        try:
            actual_node = device.xpath(
                "//android.widget.TextView[@text=\"What's new?\"]/preceding-sibling::android.widget.TextView[1]"
            )
            if not actual_node.exists:
                # Si no existe en inglés, prueba en español
                actual_node = device.xpath(
                    "//android.widget.TextView[@text='¿Qué hay de nuevo?']/preceding-sibling::android.widget.TextView[1]"
                )
            if actual_node.exists:
                actual_text = actual_node.get_text().strip()
                log_ok(f"👤 Cuenta actual detectada: {actual_text}")
            else:
                actual_text = None
                log_warn("⚠️ No se pudo detectar cuenta actual")
        except Exception as e:
            actual_text = None
            log_warn(f"⚠️ Error detectando cuenta actual: {e}")


        # Excluir cuenta actual y las ya usadas
        disponibles = [c for c in cuentas if c["usuario"] != actual_text and c["usuario"] not in cuentas_usadas]
        if not disponibles:
            log_info("🔁 Todas las cuentas ya fueron usadas (excepto actual), reiniciando rotación.")
            cuentas_usadas.clear()
            disponibles = [c for c in cuentas if c["usuario"] != actual_text]

        cuenta = random.choice(disponibles)
        cuentas_usadas.add(cuenta["usuario"])
        user = cuenta["usuario"]

        # Scroll para asegurar barra
        x = random.randint(300, 400)
        y1, y2 = 500, 1400
        device.swipe(x, y1, x + random.randint(-20, 20), y2 + random.randint(-20, 20),
                     steps=random.randint(5, 12))
        time.sleep(random.uniform(1.0, 2.5))

        # Cambiar de cuenta
        sel = (device(description="Profile") or
               device(resourceId="barcelona_tab_profile") or
               device(resourceId="com.instagram.barcelona:id/barcelona_tab_profile") or
               device(description="Perfil"))

        if not sel.wait(timeout=5):
            continue

        b = sel.info["bounds"]
        cx = (b["left"] + b["right"]) // 2 + random.randint(-5, 5)
        cy = (b["top"] + b["bottom"]) // 2 + random.randint(-5, 5)
        long_press_shell(device.serial, cx, cy, int(random.uniform(1.2, 1.8) * 1000))
        time.sleep(random.uniform(1.5, 2.5))

        wrapper = device.xpath(
            f"//android.view.View[@clickable='true']"
            f"[.//android.widget.TextView[@text='{user}']]"
        )

        if wrapper.wait(timeout=5):
            wrapper.click()
            log_ok(f"🔄 Cambiado a cuenta '{user}'")
            time.sleep(random.uniform(1.5, 3.0))
        else:
            device.press("back")
            continue

        revisar_threads_primer_plano(device)

        likes_hechos = 0
        max_likes = random.randint(1, max_likes_por_cuenta)

        while likes_hechos < max_likes and time.time() < fin:
            botones = device.xpath(
                "//android.widget.Button[contains(@resource-id,'feed_post_ufi_like_button')]"
            ).all()
            random.shuffle(botones)
            for btn in botones:
                if likes_hechos >= max_likes or time.time() >= fin:
                    break
                if random.random() < like_prob:
                    info = btn.info["bounds"]
                    tap_with_jitter(device, info)
                    likes_hechos += 1
                    log_ok(f"[{user}] 🧡 Like #{likes_hechos}")
                    time.sleep(random.uniform(3.0, 8.0))

            x0 = random.randint(300, 400)
            y_start = random.randint(1300, 1400)
            y_end = random.randint(300, 400)
            device.swipe(x0, y_start, x0 + random.randint(-20, 20),
                         y_end + random.randint(-20, 20),
                         steps=random.randint(6, 12))
            time.sleep(random.uniform(5.0, 12.0))

            if random.random() < 0.01:
                try:
                    btn_reply = device.xpath(
                        "(//android.widget.Button[contains(@resource-id,'feed_post_ufi_reply_button')])[1]"
                    )
                    if btn_reply.exists:
                        btn_reply.get().click()
                        log_ok(f"[{user}] 💬 Abrí diálogo de comentarios")
                        time.sleep(random.uniform(1.2, 2.5))
                        campo = device(className="android.widget.EditText")
                        if campo.wait(timeout=5):
                            texto = random.choice(comentarios)
                            campo.set_text(texto)
                            log_ok(f"[{user}] ✍️ Comentario: {texto}")
                            post_btn = device(resourceId="permalink_inline_composer_post_button")
                            if post_btn.exists:
                                post_btn.click()
                                log_ok(f"[{user}] 📤 Comentario enviado")
                            else:
                                log_warn(f"[{user}] ⚠️ Botón de publicar no encontrado")
                            time.sleep(random.uniform(1.5, 3.0))
                        else:
                            log_warn(f"[{user}] ⚠️ Campo de comentario no apareció")
                        device.press("back")
                        time.sleep(1)
                except Exception:
                    pass

            remaining = fin - time.time()
            mins, secs = divmod(int(remaining), 60)
            try:
                with open(get_countdown_file(), "w", encoding="utf-8") as f:
                    json.dump({
                        "remaining": int(remaining),
                        "mins": mins,
                        "secs": secs,
                        "total": segundos_totales
                    }, f)
            except Exception:
                pass

            revisar_threads_primer_plano(device)

        descanso = random.uniform(30.0, 90.0)
        log_info(f"[{user}] 💤 Descansando {descanso:.1f}s antes de la siguiente cuenta")
        time.sleep(descanso)

    device.swipe(360, 400, 360, 1400, steps=10)
    time.sleep(1)
    try:
        os.remove(get_countdown_file())
    except:
        pass

"""
        # 2) Replies
        """

def publicar_con_u2(udid, entradas, mensajes_texto, cuentas, espera_segundos):
    safe = safe_udid(udid)
    thread_local.log_path = LOG_PATH_TEMPLATE % safe
    thread_local.countdown_file = COUNTDOWN_FILE_TEMPLATE % safe
    with open(thread_local.log_path, "w", encoding="utf-8") as f:
        f.write("📄 Log de publicaciones\n========================\n")

    SOCIAL_CADA_N_CICLOS = (8, 11)
    proximo_social = random.randint(*SOCIAL_CADA_N_CICLOS)
    ciclos_completados = 0
    d = connect_device(udid)
    while True:
        try:
            if not cuentas:
                log_warn(f"[{udid}] Sin cuentas, abortando")
                return

            # desactivar animaciones
            for cmd in (
                "settings put global window_animation_scale 0",
                "settings put global transition_animation_scale 0",
                "settings put global animator_duration_scale 0"
            ):
                d.shell(cmd)
            log_ok(f"[{udid}] Animaciones OFF")
    
            d.app_start("com.instagram.barcelona")
            time.sleep(2)

            idx=0
            for cuenta in cuentas:
                username = cuenta["usuario"]
                log_info(f"[{udid}] Publicando en {username}")
                # consulta el package actual correctamente
                curr = d.app_current().get("packageName", "")
                if curr != "com.instagram.barcelona":
                    d.app_start("com.instagram.barcelona")
                    time.sleep(1)



                # 1) Localiza el tab Perfil (por content-desc o resource-id)
                # Scroll para asegurar barra
                x = random.randint(300, 400)
                y1, y2 = 500, 1400
                d.swipe(x, y1, x + random.randint(-20, 20), y2 + random.randint(-20, 20),
                            steps=random.randint(5, 12))
                time.sleep(random.uniform(1.0, 2.5))

                with device_locks[udid]:
                    # Cambiar de cuenta
                    sel = (d(description="Profile") or
                        d(resourceId="barcelona_tab_profile") or
                        d(resourceId="com.instagram.barcelona:id/barcelona_tab_profile") or
                        d(description="Perfil"))

                    # 2) Long-press en el centro del selector
                    b = sel.info["bounds"]
                    x = (b["left"] + b["right"]) // 2
                    y = (b["top"]  + b["bottom"]) // 2
                    long_press_shell(udid, x, y, int(1.5*1000))
                    time.sleep(3)

                    # 3) Espera y click en la cuenta
                    # localizamos el contenedor completo de la fila de la cuenta
                    wrapper = d.xpath(f"//android.view.View[@clickable='true'][.//android.widget.TextView[@text='{username}']]")
                    if wrapper.wait(timeout=8):
                        # buscamos un descendiente con content-desc "Current account"
                        if idx==0:
                            log_ok(f"[{udid}] Ya en '{username}', no cambio de cuenta")
                            d.press("back")
                            idx += 1
                            time.sleep(1)
                        else:
                            if not wrapper.click():
                                log_ok(f"[{udid}] Cambio a cuenta '{username}' correcto")
                                time.sleep(1)
                            else:
                                log_error(f"[{udid}] Falla al cambiar a '{username}'")
                                d.press("back")
                                idx += 1
                                continue
                    else:
                        log_error(f"[{udid}] No encontré el contenedor para '{username}'")
                        d.press("back")
                        continue
    
                # 4) Botón Create (hasta 3 intentos)
                success = False
    
                opciones = [
                    d(resourceId="barcelona_tab_create"),
                    d(resourceId="com.instagram.barcelona:id/barcelona_tab_create") 
                ]

                for intento in range(1, 5):
                    sel = None
                    for opcion in opciones:
                        if opcion.exists:
                            sel = opcion
                            break

                    if sel:
                        if sel.wait(timeout=10):
                            try:
                                sel.click()
                                log_ok(f"[{udid}] Click en 'Create' realizado correctamente (intento {intento})")
                                success = True
                                break
                            except Exception as e:
                                log_warn(f"[{udid}] ⚠️ Falló el click en 'Create' (intento {intento}): {e}")
                        else:
                            log_warn(f"[{udid}] ❌ El botón 'Create' no apareció (intento {intento})")
                    else:
                        log_warn(f"[{udid}] ❌ No encontré el botón 'Create' (intento {intento})")

                    time.sleep(1.5)

                if not success:
                    log_error(f"[{udid}] ❌ Tras 3 intentos no se pudo hacer click en 'Create', saltando cuenta")
                    #d.app_start("com.instagram.barcelona")
                    #time.sleep(2)
                    continue
    
                entrada = random.choice(entradas)
                tipo = entrada["tipo"]

                

                if tipo == "mensaje":
                    texto = entrada["mensaje"] or ""
                    campo = d(className="android.widget.EditText")
                    campo.set_text(texto)
                    log(f"[{udid}] ✍️ Mensaje: {texto}")

                    success = False
                    post_btns = [
                        d(text="Post"),
                        d(resourceId="new_thread_screen_post_button"),
                        d.xpath("//android.widget.TextView[@text='Post']")
                    ]

                    for intento in range(1, 5):
                        sel = None
                        for opcion in post_btns:
                            if opcion.exists:
                                sel = opcion
                                break

                        if sel:
                            if sel.wait(timeout=10):
                                try:
                                    sel.click()
                                    log_ok(f"[{udid}] Click en 'Post' realizado correctamente (intento {intento})")
                                    success = True
                                    break
                                except Exception as e:
                                    log_warn(f"[{udid}] ⚠️ Falló el click en 'Post' (intento {intento}): {e}")
                            else:
                                log_warn(f"[{udid}] ❌ El botón 'Post' no apareció (intento {intento})")
                        else:
                            log_warn(f"[{udid}] ❌ No encontré el botón 'Post' (intento {intento})")

                        time.sleep(1.5)

                    if not success:
                        log_error(f"[{udid}] ❌ Tras 3 intentos no se pudo hacer click en 'Post', saltando cuenta")
                        continue

                    revisar_posted(udid, d)
    
                elif tipo == "mensaje_foto":
                    # 2) Texto + foto
                    texto = entrada["mensaje"] or ""
                    foto_num = entrada["foto"]
                    if foto_num is None:
                        log_warn(f"[{udid}] No hay número de foto, salto…")
                        continue
    
                    # 2.1) subimos esa foto concreta
                    if not copiar_imagen_especifica(udid, RUTA_FOTOS, foto_num):
                        log_warn(f"[{udid}] Falló push de foto #{foto_num}, salto…")
                        continue
    
                    # 2.2) escribimos texto
                    campo = d(className="android.widget.EditText")
                    campo.set_text(texto)
                    log(f"[{udid}] ✍️ Mensaje: {texto}")
    
                    # 2.3) abrimos galería y elegimos la primera miniatura
                    if d(description="Gallery").click():
                        log_error(f"[{udid}] No encontré botón 'Gallery'")
                        continue
                    log_ok(f"[{udid}] Abrí galería")
                    # espera que aparezca la miniatura
                    if d.xpath("//android.widget.GridView/android.view.ViewGroup[1]").wait(timeout=10):
                        d.xpath("//android.widget.GridView/android.view.ViewGroup[1]").click()
                        log_ok(f"[{udid}] Seleccionada foto #{foto_num}")
                    else:
                        log_error(f"[{udid}] No pude seleccionar la foto")
                        continue
    
                    # 2.4) pulsar Done si aparece el botón
                    if d(text="Done").exists:
                        d(text="Done").click()
    
                    # 2.5) publicar
                    d(text="Post").click()
                    log_ok(f"[{udid}] Pulsado 'Post' para mensaje+foto")
                    revisar_posted(udid, d)
    
                elif tipo == "encuesta":
                    pregunta = entrada["pregunta"]
                    op1 = entrada["opcion1"]
                    op2 = entrada["opcion2"]
                    if not (pregunta and op1 and op2):
                        log_warn(f"[{udid}] Datos de encuesta incompletos, salto…")
                        continue
    
                    # 1) Pulsar botón "Poll"
                    if d(description="Poll").click():
                        log_error(f"[{udid}] No encontré botón 'Poll'")
                        continue
                    log_ok(f"[{udid}] Abrí encuesta")
    
                   # 1) Espera a que aparezcan los 3 EditText (pregunta + 2 opciones)
                    fields = d(className="android.widget.EditText")
                    if not fields.wait(timeout=10):
                        log_error(f"[{udid}] ❌ No aparecieron EditText para encuesta")
                        d.press("back")
                        continue
    
                    # Asegúrate de que haya al menos 3
                    if len(fields) < 3:
                        log_error(f"[{udid}] ❌ Solo encontré {len(fields)} EditText, necesito 3")
                        d.press("back")
                        continue
    
                    # 2) Rellenar pregunta
                    f0 = fields[0]               # primer EditText → pregunta
                    f0.click(); time.sleep(0.3)
                    f0.clear_text()
                    f0.set_text(pregunta)
                    log(f"[{udid}] ✍️ Pregunta: {pregunta}")
    
                    # 3) Rellenar opción 1
                    f1 = fields[1]
                    f1.click(); time.sleep(0.3)
                    f1.clear_text()
                    f1.set_text(op1)
                    log(f"[{udid}] ✍️ Opción1: {op1}")
    
                    # 4) Rellenar opción 2
                    f2 = fields[2]
                    f2.click(); time.sleep(0.3)
                    f2.clear_text()
                    f2.set_text(op2)
                    log(f"[{udid}] ✍️ Opción2: {op2}")
    
                    # 5) Pulsar Post
                    if not d.xpath("//android.widget.TextView[@text='Post']").click(timeout=5):
                        log_ok(f"[{udid}] 📤 Encuesta publicada con éxito")
                    else:
                        log_error(f"[{udid}] ❌ No pude pulsar 'Post'")
                        d.press("back")
                        continue
    
    
    
                    revisar_posted(udid, d)
    
    
                else:
                    log_warn(f"[{udid}] Tipo desconocido '{tipo}', salto…")
                    continue
    
                time.sleep(3)  # breve pausa entre cuentas
    
            idx=0

            ciclos_completados += 1
            if use_social and (ciclos_completados >= proximo_social):
                log_ok(f"[{udid}] 🏁 Ciclo #{ciclos_completados} → descanso social de {espera_segundos}s")
                esperar_tiempo_social_humano(
                    espera_segundos,
                    d,
                    mensajes_texto,
                    cuentas,
                    like_prob=0.1,
                    max_likes_por_cuenta=4
                )
                log_ok(f"[{udid}] 🤖 Descanso social finalizado")

                 # Reinicia contador y elige nuevo umbral aleatorio
                ciclos_completados = 0
                proximo_social = random.randint(*SOCIAL_CADA_N_CICLOS)
                log_info(f"[{udid}] 🔄 Próximo descanso social dentro de {proximo_social} ciclos")

            else:
                log_info(f"[{udid}] 😴 Ciclo #{ciclos_completados} → descanso normal de {espera_segundos}s")
                esperar_tiempo(espera_segundos)

        except Exception as e:
            log_error(f"[{udid}] ¡EXCEPCIÓN no esperada!: \nReiniciando app y reintentando… {e}\n{traceback.format_exc()}")
            try:
                d = connect_device(udid)
                d.app_stop("com.instagram.barcelona")
                time.sleep(2)
                d.app_start("com.instagram.barcelona")
                time.sleep(4)

                if os.path.exists(get_countdown_file()):
                    with open(get_countdown_file()) as f:
                        data = json.load(f)
                    rem = data.get("remaining", 0)
                    if rem > 0:
                        log_info(f"[{udid}] Reiniciando espera de {rem}s tras fallo")
                        if use_social:
                            esperar_tiempo_social_humano(rem, d, mensajes_texto, cuentas,
                                                        like_prob=0.1, max_likes_por_cuenta=4)
                        else:
                            esperar_tiempo(rem)
                        try:
                            os.remove(get_countdown_file())
                        except:
                            pass
                        continue  # saltamos el repost y vamos al siguiente ciclo
            
            except Exception as e2:
                log_error(f"[{udid}] Error reconectando dispositivo: {e2}\n{traceback.format_exc()}")
            continue

def main():
    # limpiar countdown
    if os.path.exists(DEFAULT_COUNTDOWN_FILE):
        os.remove(DEFAULT_COUNTDOWN_FILE)

    with open(DEFAULT_LOG_PATH, "w", encoding="utf-8") as f:
        f.write("📄 Log de publicaciones\n========================\n")

    subprocess.run("adb kill-server && adb start-server", shell=True)

    espera_segundos = 600
    if "--wait" in sys.argv:
        i = sys.argv.index("--wait")
        espera_segundos = int(sys.argv[i+1])
        sys.argv.pop(i); sys.argv.pop(i)

    excel = "mensajes.xlsx"
    entradas = cargar_entradas_con_tipo(excel)
    mensajes_texto = cargar_mensajes_texto(excel)

    if not entradas:
        log_warn("Sin entradas en Excel, saliendo")
        return

    dispositivos = obtener_dispositivos_usb()
    log_info(f"Dispositivos: {dispositivos}")

    # 1) Registra en memoria, sin pisar
    todas_cuentas = []
    for udid in dispositivos:
        d = connect_device(udid)
        d.app_start("com.instagram.barcelona")
        usuarios = registrar_cuentas_dispositivo_u2(d, udid)
        for u in usuarios:
            todas_cuentas.append({
                "usuario": u,
                "correo": "",
                "contrasena": "",
                "device": udid
            })


    # 2) Guarda TODO el JSON de golpe
    with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump(todas_cuentas, f, indent=2, ensure_ascii=False)

    # 3) construir un dict que asocie cada UDID con su lista
    cuentas_por_dispositivo = { ud: [] for ud in dispositivos }
    for c in todas_cuentas:
        cuentas_por_dispositivo[c["device"]].append(c)


    log_info(f"🏁 Arrancando en modo {'USB' if use_usb else 'AVD'} – espera={espera_segundos}s – cuentas={len(todas_cuentas)} – fotos en {RUTA_FOTOS}")

    threads = []
    for udid in dispositivos:
        device_locks[udid] = threading.Lock()
        grupo = cuentas_por_dispositivo.get(udid, [])
        t = threading.Thread(
            target=publicar_con_u2,
            args=(udid, entradas, mensajes_texto, grupo, espera_segundos),
            daemon=False
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

if __name__ == "__main__":
    main()
