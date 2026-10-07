"""
mqtt_simulator.py — Simulador de cocinas virtuales
Universidad Industrial de Santander (UIS) — 2026
Autor: Cesar Daniel Ávila Barbosa

Rol: genera datos sintéticos realistas para N cocinas virtuales
     y los publica en MQTT, permitiendo validar la plataforma
     sin necesidad de hardware físico múltiple.

Uso:
    python mqtt_simulator.py              # 3 cocinas, modo aleatorio
    python mqtt_simulator.py --cocinas 10 # 10 cocinas simultáneas
    python mqtt_simulator.py --escenario gas_critico --cocina cocina_02
"""

import os
import sys
import json
import time
import math
import random
import argparse
import logging
import signal
import paho.mqtt.client as mqtt

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mqtt_sim")

# ── Configuración ─────────────────────────────────────────────────
BROKER    = os.getenv("MQTT_BROKER", "localhost")
PORT      = int(os.getenv("MQTT_PORT", "1883"))


# ── Escenarios predefinidos ────────────────────────────────────────
# Cada escenario define el comportamiento de una cocina virtual.
# Los valores son (valor_inicial, variación_por_ciclo, límite_max)
ESCENARIOS = {
    "normal": {
        "descripcion": "Cocina en operación normal con carga moderada",
        "temp_base": 28.0, "temp_var": 0.3, "temp_max": 45.0,
        "gas_base":  200,  "gas_var":  10,  "gas_max":  400,
        "pres_base": 0.3,  "pres_var": 0.02,
        "llama_prob": 0.0,
    },
    "carga_alta": {
        "descripcion": "Cocina con todos los fogones al máximo",
        "temp_base": 42.0, "temp_var": 0.5, "temp_max": 65.0,
        "gas_base":  350,  "gas_var":  15,  "gas_max":  600,
        "pres_base": 0.45, "pres_var": 0.03,
        "llama_prob": 0.0,
    },
    "prealerta_gas": {
        "descripcion": "Fuga leve de gas — prealerta P4",
        "temp_base": 30.0, "temp_var": 0.2, "temp_max": 40.0,
        "gas_base":  850,  "gas_var":  20,  "gas_max":  1500,
        "pres_base": 0.35, "pres_var": 0.02,
        "llama_prob": 0.0,
    },
    "gas_critico": {
        "descripcion": "Fuga crítica de gas — emergencia P1",
        "temp_base": 28.0, "temp_var": 0.1, "temp_max": 35.0,
        "gas_base":  1700, "gas_var":  30,  "gas_max":  3500,
        "pres_base": 0.5,  "pres_var": 0.05,
        "llama_prob": 0.0,
    },
    "incendio_moderado": {
        "descripcion": "Incendio moderado con llama detectada — P3",
        "temp_base": 65.0, "temp_var": 1.5, "temp_max": 95.0,
        "gas_base":  300,  "gas_var":  5,   "gas_max":  500,
        "pres_base": 0.55, "pres_var": 0.04,
        "llama_prob": 1.0,
    },
    "incendio_severo": {
        "descripcion": "Incendio severo — P2",
        "temp_base": 92.0, "temp_var": 2.0, "temp_max": 130.0,
        "gas_base":  200,  "gas_var":  5,   "gas_max":  350,
        "pres_base": 0.7,  "pres_var": 0.05,
        "llama_prob": 1.0,
    },
    "explosion_inminente": {
        "descripcion": "Gas crítico + llama — P0 CRÍTICA",
        "temp_base": 75.0, "temp_var": 1.0, "temp_max": 110.0,
        "gas_base":  1750, "gas_var":  20,  "gas_max":  3000,
        "pres_base": 0.65, "pres_var": 0.04,
        "llama_prob": 1.0,
    },
}


# ── Estado de cada cocina virtual ─────────────────────────────────
class CocinaVirtual:
    def __init__(self, cocina_id: str, escenario: str):
        self.id          = cocina_id
        self.escenario   = ESCENARIOS.get(escenario, ESCENARIOS["normal"])
        self.temp        = float(self.escenario["temp_base"])
        self.gas         = float(self.escenario["gas_base"])
        self.pres        = float(self.escenario["pres_base"])
        self.ciclo       = 0
        self.ts_inicio   = int(time.time() * 1000)

    def siguiente_lectura(self) -> dict:
        """Genera la siguiente lectura simulada con variación realista."""
        e = self.escenario
        self.ciclo += 1

        # Temperatura: sube gradualmente con ruido gaussiano
        self.temp += random.gauss(e["temp_var"], e["temp_var"] * 0.3)
        self.temp  = min(self.temp, e["temp_max"])
        self.temp  = max(self.temp, e["temp_base"] * 0.8)

        # Gas: sube con ruido + tendencia según escenario
        self.gas += random.gauss(e["gas_var"], e["gas_var"] * 0.5)
        self.gas  = min(self.gas, e["gas_max"])
        self.gas  = max(self.gas, 100)

        # Presión: oscilación sinusoidal + ruido
        self.pres = (
            e["pres_base"]
            + e["pres_var"] * math.sin(self.ciclo * 0.2)
            + random.gauss(0, e["pres_var"] * 0.2)
        )
        self.pres = max(0.1, min(self.pres, 1.0))

        # Llama: probabilidad según escenario
        llama = random.random() < e["llama_prob"]

        # Convertir presión relativa (0-1) a raw ADC (0-4095)
        pres_raw = int(self.pres * 4095)
        gas_raw  = int(self.gas)
        temp_c   = round(self.temp, 1)

        # Calcular prioridad (misma lógica que el firmware)
        prio = self._calcular_prioridad(temp_c, gas_raw, pres_raw, llama)

        # Calcular actuadores según prioridad
        v1, v2, v3, valvula, aspersor = self._calcular_actuadores(
            prio, temp_c, gas_raw, pres_raw
        )

        ts = int(time.time() * 1000) - self.ts_inicio

        return {
            "temperatura":           temp_c,
            "gas_raw":               gas_raw,
            "gas_pct":               int(gas_raw / 4095 * 100),
            "presion_raw":           pres_raw,
            "presion_pct":           int(pres_raw / 4095 * 100),
            "llama":                 llama,
            "delta_t":               round(self.escenario["temp_var"], 2),
            "prioridad":             prio,
            "v1_pct":                v1,
            "v2_pct":                v2,
            "v3_pct":                v3,
            "valvula":               valvula,
            "aspersor":              aspersor,
            "confirmando":           False,
            "cocina_id":             self.id,
            "escenario":             list(ESCENARIOS.keys())[
                                       list(ESCENARIOS.values()).index(self.escenario)
                                     ],
            "ts":                    ts,
        }

    def _calcular_prioridad(self, temp, gas, pres, llama) -> int:
        GAS_LEL_LOW  = 800
        GAS_LEL_HIGH = 1640
        PRES_ALTA    = 2786

        gas_alerta    = gas >= GAS_LEL_LOW
        gas_emergencia = gas >= GAS_LEL_HIGH
        pres_alta     = pres >= PRES_ALTA
        dt_rapido     = self.escenario["temp_var"] >= 2.0

        if llama and gas_emergencia:           return 0
        if gas_emergencia and not llama:       return 1
        if llama and temp > 90:                return 2
        if llama and (temp >= 60 or dt_rapido): return 3
        if pres_alta and gas_alerta:           return 5
        if gas_alerta and not gas_emergencia:  return 4
        if llama and temp < 60 and not dt_rapido: return 8
        if pres_alta and not gas_alerta:       return 6
        if temp > 20:                          return 7
        return 9

    def _calcular_actuadores(self, prio, temp, gas, pres):
        """Devuelve (v1%, v2%, v3%, valvula, aspersor)"""
        if prio == 0: return  0,  0,  0, True,  True
        if prio == 1: return 100,100,100, True, False
        if prio == 2: return  0,  0,  0, True,  True
        if prio == 3: return  0,100,  0, True,  True
        if prio == 4: return 70, 70,  0, False, False
        if prio == 5: return  0,100,100, False, False
        if prio == 6:
            base = max(30, min(int((temp-20)/(60-20)*70+30), 100))
            return int(base*0.65), min(int(base*1.35),100), 0, False, False
        if prio <= 8:
            pwm = max(30, min(int((temp-20)/(60-20)*70+30), 100)) if temp > 20 else 20
            return pwm, pwm, 0, False, False
        return 0, 0, 0, False, False


# ── Publicador ────────────────────────────────────────────────────
class Publicador:
    def __init__(self, cocinas: list[CocinaVirtual], intervalo: float = 5.0):
        self.cocinas   = cocinas
        self.intervalo = intervalo
        self.corriendo = True

        self.client = mqtt.Client(client_id="simulator", clean_session=True)
        self.client.on_connect = self._on_connect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info(f"Simulador conectado al broker {BROKER}:{PORT}")
        else:
            log.error(f"Error de conexión: rc={rc}")

    def arrancar(self):
        try:
            self.client.connect(BROKER, PORT, 60)
            self.client.loop_start()
        except ConnectionRefusedError:
            log.error(f"Broker no disponible en {BROKER}:{PORT}")
            sys.exit(1)

        log.info(
            f"Simulando {len(self.cocinas)} cocina(s) "
            f"cada {self.intervalo}s — Ctrl+C para detener"
        )

        def shutdown(sig, frame):
            log.info("Deteniendo simulador...")
            self.corriendo = False
            self.client.loop_stop()
            self.client.disconnect()
            sys.exit(0)

        signal.signal(signal.SIGINT,  shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        while self.corriendo:
            for cocina in self.cocinas:
                lectura  = cocina.siguiente_lectura()
                topic    = f"cocinas/{cocina.id}"
                payload  = json.dumps(lectura)
                prio     = lectura["prioridad"]

                self.client.publish(topic, payload, qos=1)

                icono = "🔴" if prio <= 1 else "🟠" if prio <= 3 else "🟡" if prio <= 5 else "🟢"
                log.info(
                    f"{icono} [{cocina.id}] P{prio} "
                    f"T:{lectura['temperatura']}°C "
                    f"G:{lectura['gas_pct']}% "
                    f"L:{'SÍ' if lectura['llama'] else 'no'}"
                )

            time.sleep(self.intervalo)


# ── CLI ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Simulador de cocinas virtuales para el sistema IoT"
    )
    parser.add_argument(
        "--cocinas", type=int, default=3,
        help="Número de cocinas virtuales a simular (default: 3)"
    )
    parser.add_argument(
        "--escenario", type=str, default="normal",
        choices=list(ESCENARIOS.keys()),
        help="Escenario para todas las cocinas (default: normal)"
    )
    parser.add_argument(
        "--cocina", type=str, default=None,
        help="ID específico de cocina (solo con --escenario)"
    )
    parser.add_argument(
        "--intervalo", type=float, default=5.0,
        help="Segundos entre envíos (default: 5.0)"
    )
    parser.add_argument(
        "--listar", action="store_true",
        help="Lista los escenarios disponibles y sale"
    )
    args = parser.parse_args()

    if args.listar:
        print("\nEscenarios disponibles:\n")
        for nombre, esc in ESCENARIOS.items():
            print(f"  {nombre:<25} {esc['descripcion']}")
        print()
        return

    # Construir lista de cocinas
    if args.cocina:
        # Una cocina específica con el escenario indicado
        cocinas = [CocinaVirtual(args.cocina, args.escenario)]
    else:
        # N cocinas con escenarios variados
        escenarios_list = list(ESCENARIOS.keys())
        cocinas = [
            CocinaVirtual(
                f"cocina_{str(i+1).zfill(2)}",
                escenarios_list[i % len(escenarios_list)]
            )
            for i in range(args.cocinas)
        ]

    log.info("Cocinas configuradas:")
    for c in cocinas:
        desc = c.escenario["descripcion"]
        log.info(f"  {c.id} → {desc}")

    pub = Publicador(cocinas, args.intervalo)
    pub.arrancar()


if __name__ == "__main__":
    main()
