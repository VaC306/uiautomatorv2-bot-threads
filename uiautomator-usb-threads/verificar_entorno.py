import subprocess
import shutil
import os
import time
import signal
import sys
import psutil

def comprobar_comando(nombre):
    ruta = shutil.which(nombre)
    if ruta:
        print(f"✅ '{nombre}' está instalado en: {ruta}")
        return True
    else:
        print(f"❌ '{nombre}' no está disponible en el PATH del sistema.")
        return False

def version_comando(nombre):
    try:
        resultado = subprocess.run(f"{nombre} --version", capture_output=True, text=True, shell=True)
        if resultado.returncode == 0:
            print(f"ℹ️  {nombre} versión: {resultado.stdout.strip()}")
        else:
            print(f"⚠️  Error ejecutando '{nombre} --version'")
    except Exception as e:
        print(f"⚠️  No se pudo verificar la versión de '{nombre}': {e}'")

def comprobar_uiautomator2_driver():
    try:
        resultado = subprocess.run("appium driver list", capture_output=True, text=True, shell=True)
        output = (resultado.stdout + resultado.stderr).lower()
        if "uiautomator2" in output and "installed" in output:
            print("✅ appium-uiautomator2-driver está instalado correctamente.")
        else:
            print("❌ appium-uiautomator2-driver no está instalado o no se detecta.")
            print("💡 Instálalo con: appium driver install uiautomator2")
    except Exception as e:
        print(f"⚠️ No se pudo verificar appium-uiautomator2-driver: {e}")

def comprobar_vars_entorno():
    android_home = os.environ.get("ANDROID_HOME")
    sdk_root = os.environ.get("ANDROID_SDK_ROOT")

    if android_home:
        print(f"✅ ANDROID_HOME = {android_home}")
    else:
        print("❌ Variable ANDROID_HOME no está definida.")

    if sdk_root:
        print(f"✅ ANDROID_SDK_ROOT = {sdk_root}")
    else:
        print("❌ Variable ANDROID_SDK_ROOT no está definida.")

    for tool in ["adb", "emulator", "aapt"]:
        path = shutil.which(tool)
        if path:
            print(f"✅ {tool} está en el PATH: {path}")
        else:
            print(f"❌ {tool} no está en el PATH.")

def cerrar_ventanas_appium_emuladores():
    print("\n🧹 Cerrando procesos abiertos de Appium y emuladores...\n")
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['cmdline']:
                cmd = " ".join(proc.info['cmdline']).lower()
                if "appium" in cmd or "emulator" in cmd:
                    print(f"⛔ Cerrando proceso PID {proc.pid}: {proc.name()}")
                    proc.send_signal(signal.SIGTERM)
        except Exception:
            continue
    time.sleep(1)
    print("✅ Limpieza completada.\n")

def asegurar_dependencias():
    print("🔧 Comprobando dependencias de Python...\n")
    dependencias = {
        "psutil": "psutil",
        "openpyxl": "openpyxl",
        "appium": "Appium-Python-Client",
        "uiautomator2": "uiautomator2"
    }

    for modulo, paquete in dependencias.items():
        try:
            __import__(modulo)
            print(f"✅ Módulo '{modulo}' está instalado.")
        except ImportError:
            print(f"❌ Módulo '{modulo}' no está instalado. Instalando '{paquete}'...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", paquete])
                print(f"✅ '{paquete}' instalado correctamente.")
            except subprocess.CalledProcessError:
                print(f"❌ Error al instalar '{paquete}'. Instálalo manualmente.")

def verificar_dispositivos_adb():
    print("📱 Verificando dispositivos conectados por ADB...")
    try:
        salida = subprocess.check_output("adb devices", shell=True, text=True)
        dispositivos = [line for line in salida.splitlines() if "device" in line and not "List" in line]
        if dispositivos:
            print(f"✅ {len(dispositivos)} dispositivo(s) detectado(s):")
            for disp in dispositivos:
                print("   -", disp)
        else:
            print("⚠️  No se detectaron dispositivos ADB. ¿Está habilitada la depuración USB?")
    except Exception as e:
        print(f"❌ Error al ejecutar adb: {e}")

def main():
    asegurar_dependencias()
    print("🔍 Verificando entorno para ejecutar el bot de Threads:\n")

    todo_ok = True

    cerrar_ventanas_appium_emuladores()

    if comprobar_comando("npm"):
        version_comando("npm")
    else:
        todo_ok = False
        print("💡 Instala Node.js desde: https://nodejs.org/")

    if comprobar_comando("appium"):
        version_comando("appium")
        comprobar_uiautomator2_driver()
    else:
        todo_ok = False
        print("💡 Instala Appium con: npm install -g appium")

    comprobar_vars_entorno()
    verificar_dispositivos_adb()

    if todo_ok:
        print("\n✅ Todo listo para usar el bot con Appium y dispositivos USB.")
    else:
        print("\n⚠️  Revisa los errores anteriores para que el entorno esté completamente funcional.")

if __name__ == "__main__":
    main()