"""
payload.py — Contrato y validación del payload MQTT (sin dependencias de paho ni Django).

Contrato canónico = payload que publica el firmware (esp32/README.md). Este módulo es el
único lugar donde se valida y se convierte un mensaje MQTT en los campos de una lectura,
de modo que se pueda probar sin broker ni base de datos (ver test_offline.py).
"""
import json
import math
import re

MAX_BYTES = 2048                      # el payload real mide ~300 bytes
TOPIC_RE = re.compile(r"^cocinas/([A-Za-z0-9_-]{1,32})$")

# Rangos de validez (el ADC del ESP32 es de 12 bits; el firmware considera válido el NTC en -50..300 °C)
TEMP_MIN, TEMP_MAX = -55.0, 300.0

CAMPOS_REQUERIDOS = {
    "temperatura", "gas_raw", "presion_raw",
    "llama", "prioridad", "v1_pct", "v2_pct", "v3_pct",
}

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


def _num(x):
    """True si x es int/float real y finito (bool NO cuenta como número)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _entero(x, lo, hi):
    if not _num(x) or (isinstance(x, float) and not x.is_integer()):
        return False
    return lo <= x <= hi


def validar_payload(data) -> tuple[bool, str]:
    """Valida tipos y rangos. Nunca lanza excepciones: devuelve (False, motivo)."""
    if not isinstance(data, dict):
        return False, "El payload no es un objeto JSON"
    faltantes = CAMPOS_REQUERIDOS - set(data.keys())
    if faltantes:
        return False, f"Campos faltantes: {sorted(faltantes)}"

    t = data["temperatura"]
    if not _num(t) or not (TEMP_MIN <= t <= TEMP_MAX):
        return False, f"Temperatura inválida: {t!r}"
    if not _entero(data["prioridad"], 0, 9):
        return False, f"Prioridad inválida: {data['prioridad']!r}"
    for campo in ("gas_raw", "presion_raw"):
        if not _entero(data[campo], 0, 4095):
            return False, f"{campo} fuera de 0..4095: {data[campo]!r}"
    for campo in ("v1_pct", "v2_pct", "v3_pct", "gas_pct", "presion_pct"):
        if campo in data and not _entero(data[campo], 0, 100):
            return False, f"{campo} fuera de 0..100: {data[campo]!r}"
    if "delta_t" in data and not (_num(data["delta_t"]) and abs(data["delta_t"]) <= 1000):
        return False, f"delta_t inválido: {data['delta_t']!r}"
    if "ts" in data and not _entero(data["ts"], 0, 2**53):
        return False, f"ts inválido: {data['ts']!r}"
    for campo in ("llama", "valvula", "aspersor", "confirmando", "sensor_ok"):
        if campo in data and not isinstance(data[campo], bool):
            return False, f"{campo} debe ser booleano: {data[campo]!r}"
    return True, "OK"


def cocina_desde_topic(topic: str):
    """'cocinas/cocina_01' → 'cocina_01'; None si el topic no cumple el formato."""
    m = TOPIC_RE.match(topic or "")
    return m.group(1) if m else None


def procesar_mensaje(topic: str, raw: bytes):
    """
    Convierte un mensaje MQTT en los campos de una lectura.
    Devuelve (campos, motivo): campos=None si el mensaje se rechaza (motivo explica por qué).
    No lanza excepciones por contenido malformado.
    """
    cocina_id = cocina_desde_topic(topic)
    if cocina_id is None:
        return None, f"Topic no válido: {topic!r} (se espera cocinas/<id>)"
    if len(raw) > MAX_BYTES:
        return None, f"Payload demasiado grande ({len(raw)} bytes)"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        return None, f"JSON inválido: {e}"
    ok, motivo = validar_payload(data)
    if not ok:
        return None, motivo

    prioridad = int(data["prioridad"])
    campos = {
        "cocina_id":   cocina_id,
        "temperatura": float(data["temperatura"]),
        "gas_raw":     int(data["gas_raw"]),
        "gas_pct":     int(data.get("gas_pct", round(data["gas_raw"] * 100 / 4095))),
        "presion_raw": int(data["presion_raw"]),
        "presion_pct": int(data.get("presion_pct", round(data["presion_raw"] * 100 / 4095))),
        "llama":       data["llama"],
        "delta_t":     float(data.get("delta_t", 0.0)),
        "prioridad":   prioridad,
        "v1_pct":      int(data["v1_pct"]),
        "v2_pct":      int(data["v2_pct"]),
        "v3_pct":      int(data["v3_pct"]),
        "valvula":     data.get("valvula", False),
        "aspersor":    data.get("aspersor", False),
        "confirmando": data.get("confirmando", False),
        "sensor_ok":   data.get("sensor_ok", True),   # firmware antiguo no lo envía
        "ts_esp32":    int(data.get("ts", 0)),        # millis() desde el arranque (NO es hora real)
        "estado_sistema": ESTADO_POR_PRIORIDAD[prioridad],
    }
    return campos, "OK"
