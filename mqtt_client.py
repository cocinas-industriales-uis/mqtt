"""
mqtt_client.py — Suscriptor MQTT para Sistema IoT Cocinas Industriales
Universidad Industrial de Santander (UIS) — 2026
Autor: Cesar Daniel Ávila Barbosa

Rol: escucha mensajes del ESP32 y cocinas virtuales,
     los valida y los persiste en la base de datos Django.

Ejecutar desde Backend_cocina/:
    source venv/bin/activate
    python cocina/mqtt_client.py
"""

import os
import sys
import json
import logging
import signal
import django
import paho.mqtt.client as mqtt
from datetime import datetime

# ── Setup Django ─────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from cocina.models import Lectura  # noqa: E402 — después del setup

# ── Configuración ─────────────────────────────────────────────────
BROKER      = os.getenv("MQTT_BROKER", "localhost")
PORT        = int(os.getenv("MQTT_PORT", "1883"))
TOPIC       = "cocinas/#"          # recibe todas las cocinas
CLIENT_ID   = "django_subscriber"
KEEPALIVE   = 60

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mqtt_client")


# ── Validación del payload ─────────────────────────────────────────
CAMPOS_REQUERIDOS = {
    "temperatura", "gas_raw", "presion_raw",
    "llama", "prioridad", "v1_pct", "v2_pct", "v3_pct",
}

def validar_payload(data: dict) -> tuple[bool, str]:
    """Valida que el payload tenga los campos mínimos y rangos correctos."""
    faltantes = CAMPOS_REQUERIDOS - set(data.keys())
    if faltantes:
        return False, f"Campos faltantes: {faltantes}"

    temp = data.get("temperatura", 0)
    if not (-55 <= temp <= 155):
        return False, f"Temperatura fuera de rango: {temp}"

    prio = data.get("prioridad", 9)
    if not (0 <= prio <= 9):
        return False, f"Prioridad inválida: {prio}"

    return True, "OK"


# ── Mapeo de prioridad a estado_sistema ───────────────────────────
ESTADO_POR_PRIORIDAD = {
    0: "CRITICA",
    1: "EMERGENCIA_GAS",
    2: "INCENDIO_SEVERO",
    3: "INCENDIO_MODERADO",
    4: "PREALERTA_GAS",
    5: "PRESION_GAS",
    6: "PRESION_ALTA",
    7: "TEMP_ALTA",
    8: "MONITOREO_LLAMA",
    9: "NORMAL",
}


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
    topic   = msg.topic
    payload_str = msg.payload.decode("utf-8", errors="replace")

    log.debug(f"Mensaje en {topic}: {payload_str[:120]}")

    # ── Deserializar JSON ──────────────────────────────────────────
    try:
        data = json.loads(payload_str)
    except json.JSONDecodeError as e:
        log.warning(f"JSON inválido en {topic}: {e}")
        return

    # ── Validar ────────────────────────────────────────────────────
    ok, motivo = validar_payload(data)
    if not ok:
        log.warning(f"Payload rechazado ({topic}): {motivo}")
        return

    # ── Identificar cocina desde el topic ─────────────────────────
    # Topic: cocinas/cocina_01 → cocina_id = "cocina_01"
    partes    = topic.split("/")
    cocina_id = partes[1] if len(partes) >= 2 else "desconocida"

    # ── Construir objeto Lectura ───────────────────────────────────
    prioridad    = int(data.get("prioridad", 9))
    confirmando  = bool(data.get("confirmando", False))
    estado       = ESTADO_POR_PRIORIDAD.get(prioridad, "NORMAL")

    # Si está confirmando, el estado es informativo
    if confirmando:
        estado = f"VERIFICANDO_{estado}"

    try:
        lectura = Lectura.objects.create(
            temperatura           = float(data["temperatura"]),
            nivel_gas             = int(data["gas_raw"]),
            presion               = float(data.get("presion_raw", 0)),
            llama_detectada       = bool(data.get("llama", False)),
            delta_t               = float(data.get("delta_t", 0.0)),
            prioridad             = prioridad,
            ventilador_inyeccion  = int(data.get("v1_pct", 0)),
            ventilador_extraccion = int(data.get("v2_pct", 0)),
            ventilador_emergencia = int(data.get("v3_pct", 0)),
            valvula_cerrada       = bool(data.get("valvula", False)),
            aspersor_activo       = bool(data.get("aspersor", False)),
            estado_sistema        = estado,
            cocina_id             = cocina_id,
            ts_esp32              = int(data.get("ts", 0)),
        )
        log.info(
            f"[{cocina_id}] P{prioridad} T:{data['temperatura']:.1f}°C "
            f"G:{data.get('gas_pct', '?')}% → guardado (id={lectura.id})"
        )

        # Alerta crítica en consola
        if prioridad <= 1:
            log.error(f"⚠  ALERTA CRÍTICA en {cocina_id}: {estado}")

    except Exception as e:
        log.error(f"Error guardando lectura de {topic}: {e}")


# ── Cliente MQTT ──────────────────────────────────────────────────
client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
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
    except ConnectionRefusedError:
        log.error(
            f"No se pudo conectar al broker en {BROKER}:{PORT}. "
            "¿Está corriendo Mosquitto? → sudo systemctl start mosquitto"
        )
        sys.exit(1)

    log.info("Escuchando mensajes... (Ctrl+C para salir)")
    client.loop_forever()
