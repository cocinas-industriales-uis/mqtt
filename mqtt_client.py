"""
mqtt_client.py — Suscriptor MQTT para Sistema IoT Cocinas Industriales
Universidad Industrial de Santander (UIS) — 2026
Autor: Cesar Daniel Ávila Barbosa

Rol: escucha mensajes del ESP32 y cocinas virtuales,
     los valida y los persiste en la base de datos Django.

Ejecutar desde la carpeta mqtt/ con el entorno del backend (Django) activo:
    SERVIDOR_DIR=../servidor python mqtt_client.py
(La validación/conversión del payload vive en payload.py.)
"""

import os
import sys
import logging
import signal
import paho.mqtt.client as mqtt

from payload import procesar_mensaje, validar_payload, ESTADO_POR_PRIORIDAD  # noqa: F401 (re-exportados)

# ── Configuración ─────────────────────────────────────────────────
BROKER      = os.getenv("MQTT_BROKER", "localhost")
PORT        = int(os.getenv("MQTT_PORT", "1883"))
TOPIC       = "cocinas/#"          # recibe todas las cocinas
CLIENT_ID   = "django_subscriber"
KEEPALIVE   = 60
# Carpeta del backend Django (repo "servidor"). Por defecto, hermana de esta carpeta.
SERVIDOR_DIR = os.path.abspath(os.getenv(
    "SERVIDOR_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor")))

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mqtt_client")

_Lectura = None


def _cargar_django():
    """Inicializa Django la primera vez que se necesita (permite probar sin Django)."""
    global _Lectura
    if _Lectura is None:
        if SERVIDOR_DIR not in sys.path:
            sys.path.insert(0, SERVIDOR_DIR)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
        import django
        django.setup()
        from cocina.models import Lectura
        _Lectura = Lectura
    return _Lectura


def guardar_lectura(campos: dict):
    """Persiste una lectura. Cierra conexiones caducadas para sobrevivir a reinicios de la BD."""
    Lectura = _cargar_django()
    from django import db
    db.close_old_connections()
    return Lectura.objects.create(**campos)


# ── Callbacks MQTT ────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    codigos = {
        0: "OK",
        1: "Versión de protocolo rechazada",
        2: "ID de cliente rechazado",
        3: "Servidor no disponible",
        4: "Usuario/contraseña incorrectos",
        5: "No autorizado",
    }
    if rc == 0:
        log.info(f"Conectado al broker MQTT en {BROKER}:{PORT}")
        client.subscribe(TOPIC)
        log.info(f"Suscrito a: {TOPIC}")
    else:
        log.error(f"Error de conexión: {codigos.get(rc, f'código {rc}')}")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning(f"Desconexión inesperada (rc={rc}) — reconectando...")


def on_message(client, userdata, msg):
    """Nunca propaga excepciones: un mensaje malformado no debe detener el suscriptor."""
    try:
        campos, motivo = procesar_mensaje(msg.topic, msg.payload)
        if campos is None:
            log.warning(f"Mensaje rechazado ({msg.topic}): {motivo}")
            return

        estado, prio, cocina_id = campos["estado_sistema"], campos["prioridad"], campos["cocina_id"]
        if not campos["sensor_ok"]:
            log.warning(f"[{cocina_id}] sensor de temperatura con falla (sensor_ok=false)")

        lectura = guardar_lectura(campos)
        log.info(
            f"[{cocina_id}] P{prio} T:{campos['temperatura']:.1f}°C "
            f"G:{campos['gas_pct']}% → guardado (id={lectura.id})"
        )
        if prio <= 1:
            log.error(f"⚠  ALERTA CRÍTICA en {cocina_id}: {estado}")
    except Exception as e:  # incluye errores de BD
        log.error(f"Error procesando mensaje de {msg.topic}: {type(e).__name__}: {e}")


# ── Cliente MQTT ──────────────────────────────────────────────────
# paho-mqtt 1.x (ver requirements.txt): la API cambió en 2.x
client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
if os.getenv("MQTT_USER"):
    client.username_pw_set(os.getenv("MQTT_USER"), os.getenv("MQTT_PASSWORD", ""))
client.on_connect    = on_connect
client.on_disconnect = on_disconnect
client.on_message    = on_message


def shutdown(sig, frame):
    log.info("Cerrando suscriptor MQTT...")
    client.disconnect()
    client.loop_stop()
    sys.exit(0)


signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)


# ── Arranque ──────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"Iniciando suscriptor MQTT → {BROKER}:{PORT}")
    try:
        client.connect(BROKER, PORT, KEEPALIVE)
    except OSError as e:  # rechazo, host inalcanzable, DNS...
        log.error(
            f"No se pudo conectar al broker en {BROKER}:{PORT} ({e}). "
            "¿Está corriendo Mosquitto? → sudo systemctl start mosquitto"
        )
        sys.exit(1)

    log.info("Escuchando mensajes... (Ctrl+C para salir)")
    client.loop_forever()
