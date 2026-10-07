"""
mqtt_test.py — Suite de pruebas del módulo MQTT
Universidad Industrial de Santander (UIS) — 2026
Autor: Cesar Daniel Ávila Barbosa

Prueba el flujo completo: publicar → recibir → validar
sin necesidad del ESP32 ni de Django.

Uso:
    python mqtt_test.py                  # todas las pruebas
    python mqtt_test.py --test conexion  # solo una prueba
    python mqtt_test.py --test payload   # solo validar payloads
"""

import os
import sys
import json
import time
import argparse
import threading
import logging
import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mqtt_test")

BROKER = os.getenv("MQTT_BROKER", "localhost")
PORT   = int(os.getenv("MQTT_PORT", "1883"))

# ── Colores para la terminal ──────────────────────────────────────
OK   = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"
INFO = "\033[94m·\033[0m"

resultados = []

def registrar(nombre, ok, detalle=""):
    simbolo = OK if ok else FAIL
    msg = f"  {simbolo} {nombre}"
    if detalle:
        msg += f" — {detalle}"
    print(msg)
    resultados.append((nombre, ok))


# ── Payloads de prueba por prioridad ─────────────────────────────
PAYLOADS_TEST = {
    "P0_critica": {
        "temperatura": 78.5, "gas_raw": 1800, "gas_pct": 44,
        "presion_raw": 2100, "presion_pct": 51, "llama": True,
        "delta_t": 1.2, "prioridad": 0,
        "v1_pct": 0, "v2_pct": 0, "v3_pct": 0,
        "valvula": True, "aspersor": True, "confirmando": False,
        "ts": 5000,
    },
    "P1_gas": {
        "temperatura": 28.0, "gas_raw": 1700, "gas_pct": 41,
        "presion_raw": 1400, "presion_pct": 34, "llama": False,
        "delta_t": 0.1, "prioridad": 1,
        "v1_pct": 100, "v2_pct": 100, "v3_pct": 100,
        "valvula": True, "aspersor": False, "confirmando": False,
        "ts": 10000,
    },
    "P3_incendio_mod": {
        "temperatura": 72.0, "gas_raw": 300, "gas_pct": 7,
        "presion_raw": 1800, "presion_pct": 44, "llama": True,
        "delta_t": 2.5, "prioridad": 3,
        "v1_pct": 0, "v2_pct": 100, "v3_pct": 0,
        "valvula": True, "aspersor": True, "confirmando": False,
        "ts": 15000,
    },
    "P7_normal": {
        "temperatura": 35.0, "gas_raw": 250, "gas_pct": 6,
        "presion_raw": 1200, "presion_pct": 29, "llama": False,
        "delta_t": 0.2, "prioridad": 7,
        "v1_pct": 40, "v2_pct": 40, "v3_pct": 0,
        "valvula": False, "aspersor": False, "confirmando": False,
        "ts": 25000,
    },
    "confirmando_P3": {
        "temperatura": 65.0, "gas_raw": 280, "gas_pct": 7,
        "presion_raw": 1500, "presion_pct": 37, "llama": True,
        "delta_t": 0.8, "prioridad": 3,
        "v1_pct": 40, "v2_pct": 40, "v3_pct": 0,
        "valvula": False, "aspersor": False, "confirmando": True,
        "ts": 8000,
    },
}


# ── PRUEBA 1: Conexión al broker ──────────────────────────────────
def test_conexion():
    print(f"\n{INFO} Prueba 1 — Conexión al broker {BROKER}:{PORT}")
    ok = False
    motivo = ""
    try:
        c = mqtt.Client(client_id="test_conexion", clean_session=True)
        c.connect(BROKER, PORT, 5)
        c.disconnect()
        ok = True
        motivo = f"{BROKER}:{PORT} accesible"
    except ConnectionRefusedError:
        motivo = "Broker rechazó la conexión — ¿está corriendo Mosquitto?"
    except OSError as e:
        motivo = f"Error de red: {e}"
    registrar("Conexión al broker", ok, motivo)
    return ok


# ── PRUEBA 2: Validación de payloads ─────────────────────────────
def test_payloads():
    print(f"\n{INFO} Prueba 2 — Validación de estructura de payloads")

    CAMPOS = {
        "temperatura", "gas_raw", "gas_pct", "presion_raw",
        "llama", "prioridad", "v1_pct", "v2_pct", "v3_pct",
        "valvula", "aspersor", "confirmando", "ts",
    }

    todos_ok = True
    for nombre, payload in PAYLOADS_TEST.items():
        faltantes = CAMPOS - set(payload.keys())
        ok = len(faltantes) == 0
        if not ok:
            todos_ok = False
        registrar(f"Payload {nombre}", ok,
                  f"P{payload['prioridad']}" if ok else f"Faltan: {faltantes}")

    # Rangos
    for nombre, p in PAYLOADS_TEST.items():
        temp_ok = -55 <= p["temperatura"] <= 155
        prio_ok = 0 <= p["prioridad"] <= 9
        ok = temp_ok and prio_ok
        if not ok:
            todos_ok = False
        registrar(f"Rangos {nombre}", ok,
                  "T y prioridad en rango" if ok else
                  f"T:{p['temperatura']} P:{p['prioridad']}")

    return todos_ok


# ── PRUEBA 3: Publish / Subscribe ────────────────────────────────
def test_pubsub():
    print(f"\n{INFO} Prueba 3 — Publish / Subscribe (round-trip)")

    recibidos = {}
    evento = threading.Event()
    TOPICS_TEST = list(PAYLOADS_TEST.keys())

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            prio = data.get("prioridad", -1)
            recibidos[msg.topic] = prio
            if len(recibidos) >= len(TOPICS_TEST):
                evento.set()
        except Exception:
            pass

    # Suscriptor
    sub = mqtt.Client(client_id="test_sub", clean_session=True)
    sub.on_message = on_message
    try:
        sub.connect(BROKER, PORT, 5)
    except Exception as e:
        registrar("Publish/Subscribe", False, f"No se pudo conectar: {e}")
        return False

    sub.subscribe("test_cocinas/#")
    sub.loop_start()
    time.sleep(0.3)

    # Publicador
    pub = mqtt.Client(client_id="test_pub", clean_session=True)
    try:
        pub.connect(BROKER, PORT, 5)
    except Exception as e:
        sub.loop_stop(); sub.disconnect()
        registrar("Publish/Subscribe", False, f"Publisher no conectó: {e}")
        return False

    pub.loop_start()

    for nombre, payload in PAYLOADS_TEST.items():
        topic = f"test_cocinas/{nombre}"
        pub.publish(topic, json.dumps(payload), qos=1)
        time.sleep(0.05)

    # Esperar mensajes
    evento.wait(timeout=3.0)

    pub.loop_stop(); pub.disconnect()
    sub.loop_stop(); sub.disconnect()

    todos_ok = True
    for nombre, payload in PAYLOADS_TEST.items():
        topic   = f"test_cocinas/{nombre}"
        recibido = topic in recibidos
        prio_ok  = recibidos.get(topic) == payload["prioridad"]
        ok = recibido and prio_ok
        if not ok:
            todos_ok = False
        registrar(
            f"Round-trip {nombre}", ok,
            f"P{payload['prioridad']} enviado y recibido" if ok
            else f"{'no recibido' if not recibido else 'prioridad incorrecta'}"
        )

    return todos_ok


# ── PRUEBA 4: Latencia ────────────────────────────────────────────
def test_latencia():
    print(f"\n{INFO} Prueba 4 — Latencia de mensajes")

    latencias = []
    evento = threading.Event()
    NMED = 5

    def on_message(client, userdata, msg):
        t_recibido = time.time() * 1000
        try:
            data = json.loads(msg.payload.decode())
            t_enviado = data.get("_t", t_recibido)
            latencias.append(t_recibido - t_enviado)
            if len(latencias) >= NMED:
                evento.set()
        except Exception:
            pass

    sub = mqtt.Client(client_id="test_lat_sub", clean_session=True)
    sub.on_message = on_message
    try:
        sub.connect(BROKER, PORT, 5)
    except Exception as e:
        registrar("Latencia", False, f"No conectó: {e}")
        return False

    sub.subscribe("test_latencia")
    sub.loop_start()
    time.sleep(0.2)

    pub = mqtt.Client(client_id="test_lat_pub", clean_session=True)
    pub.connect(BROKER, PORT, 5)
    pub.loop_start()

    for _ in range(NMED):
        payload = json.dumps({"_t": time.time() * 1000, "dato": 42})
        pub.publish("test_latencia", payload, qos=0)
        time.sleep(0.1)

    evento.wait(timeout=3.0)
    pub.loop_stop(); pub.disconnect()
    sub.loop_stop(); sub.disconnect()

    if latencias:
        promedio = sum(latencias) / len(latencias)
        maximo   = max(latencias)
        ok = promedio < 50  # menos de 50ms en red local
        registrar(
            "Latencia promedio", ok,
            f"{promedio:.1f}ms (máx {maximo:.1f}ms) — "
            f"{'OK para tiempo real' if ok else 'alta para emergencias'}"
        )
        return ok
    else:
        registrar("Latencia", False, "No se recibieron mensajes")
        return False


# ── PRUEBA 5: Múltiples cocinas simultáneas ───────────────────────
def test_multicocina():
    print(f"\n{INFO} Prueba 5 — Múltiples cocinas simultáneas")

    N_COCINAS = 5
    recibidos_por_cocina = {f"cocina_{i:02d}": 0 for i in range(1, N_COCINAS+1)}
    N_MSG_POR_COCINA = 3
    evento = threading.Event()
    total_esperado = N_COCINAS * N_MSG_POR_COCINA

    def on_message(client, userdata, msg):
        partes = msg.topic.split("/")
        if len(partes) >= 2:
            cocina_id = partes[1]
            if cocina_id in recibidos_por_cocina:
                recibidos_por_cocina[cocina_id] += 1
        total = sum(recibidos_por_cocina.values())
        if total >= total_esperado:
            evento.set()

    sub = mqtt.Client(client_id="test_mc_sub", clean_session=True)
    sub.on_message = on_message
    try:
        sub.connect(BROKER, PORT, 5)
    except Exception as e:
        registrar("Multi-cocina", False, f"No conectó: {e}")
        return False

    sub.subscribe("test_mc/#")
    sub.loop_start()
    time.sleep(0.2)

    pub = mqtt.Client(client_id="test_mc_pub", clean_session=True)
    pub.connect(BROKER, PORT, 5)
    pub.loop_start()

    for ciclo in range(N_MSG_POR_COCINA):
        for i in range(1, N_COCINAS+1):
            topic   = f"test_mc/cocina_{i:02d}"
            payload = json.dumps({
                "temperatura": 25.0 + i,
                "gas_raw": 200 + i * 10,
                "gas_pct": 5 + i,
                "presion_raw": 1200,
                "presion_pct": 29,
                "llama": False,
                "delta_t": 0.1,
                "prioridad": 7,
                "v1_pct": 30, "v2_pct": 30, "v3_pct": 0,
                "valvula": False, "aspersor": False,
                "confirmando": False, "ts": ciclo * 5000,
            })
            pub.publish(topic, payload, qos=1)
        time.sleep(0.1)

    evento.wait(timeout=5.0)
    pub.loop_stop(); pub.disconnect()
    sub.loop_stop(); sub.disconnect()

    total = sum(recibidos_por_cocina.values())
    ok = total >= total_esperado

    for cocina_id, count in recibidos_por_cocina.items():
        registrar(
            f"Cocina {cocina_id}",
            count >= N_MSG_POR_COCINA,
            f"{count}/{N_MSG_POR_COCINA} mensajes recibidos"
        )

    return ok


# ── MAIN ──────────────────────────────────────────────────────────
PRUEBAS = {
    "conexion":   test_conexion,
    "payload":    test_payloads,
    "pubsub":     test_pubsub,
    "latencia":   test_latencia,
    "multicocina": test_multicocina,
}

def main():
    parser = argparse.ArgumentParser(
        description="Suite de pruebas del módulo MQTT"
    )
    parser.add_argument(
        "--test", choices=list(PRUEBAS.keys()),
        help="Ejecutar solo una prueba específica"
    )
    args = parser.parse_args()

    print("\n" + "═" * 52)
    print("  Suite de pruebas MQTT — Cocinas Industriales")
    print("  UIS 2026 · Cesar Daniel Ávila Barbosa")
    print("═" * 52)

    pruebas_a_correr = (
        [(args.test, PRUEBAS[args.test])]
        if args.test
        else list(PRUEBAS.items())
    )

    for nombre, fn in pruebas_a_correr:
        try:
            fn()
        except Exception as e:
            registrar(nombre, False, f"Excepción: {e}")

    # Resumen
    print("\n" + "─" * 52)
    total = len(resultados)
    aprobadas = sum(1 for _, ok in resultados if ok)
    falladas  = total - aprobadas

    if falladas == 0:
        print(f"  {OK} Todas las pruebas pasaron ({aprobadas}/{total})")
    else:
        print(f"  {FAIL} {falladas} prueba(s) fallaron ({aprobadas}/{total} OK)")
        print(f"\n  Pruebas fallidas:")
        for nombre, ok in resultados:
            if not ok:
                print(f"    {FAIL} {nombre}")
    print("─" * 52 + "\n")

    sys.exit(0 if falladas == 0 else 1)


if __name__ == "__main__":
    main()
