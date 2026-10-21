"""
test_offline.py — Pruebas SIN broker, SIN paho y SIN Django (unittest).

    python -m unittest test_offline -v        (desde mqtt/)

Cubre: contrato con el firmware real (fixture generado por esp32/test_host: `t DUMP`),
robustez ante mensajes malformados, topics, y coherencia del simulador con el contrato.
"""
import json
import os
import random
import types
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from payload import procesar_mensaje, validar_payload, ESTADO_POR_PRIORIDAD  # noqa: E402
import mqtt_simulator  # noqa: E402  (paho se importa solo al publicar)

TOPIC = "cocinas/cocina_01"


def firmware_payloads():
    with open(os.path.join(AQUI, "test_data", "firmware_payloads.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class ContratoFirmware(unittest.TestCase):
    def test_todos_los_payloads_reales_del_firmware_se_aceptan(self):
        pl = firmware_payloads()
        self.assertGreaterEqual(len(pl), 7)
        for p in pl:
            campos, motivo = procesar_mensaje(TOPIC, json.dumps(p).encode())
            self.assertIsNotNone(campos, f"{motivo}: {p}")
            self.assertEqual(campos["prioridad"], p["prioridad"])
            self.assertEqual(campos["estado_sistema"], ESTADO_POR_PRIORIDAD[p["prioridad"]])
            self.assertEqual(campos["cocina_id"], "cocina_01")

    def test_temperatura_de_incendio_real_no_se_descarta(self):
        p = [p for p in firmware_payloads() if p["temperatura"] > 150][0]
        campos, _ = procesar_mensaje(TOPIC, json.dumps(p).encode())
        self.assertIsNotNone(campos)

    def test_falla_de_sensor_se_conserva_con_sensor_ok_false(self):
        p = [p for p in firmware_payloads() if p["sensor_ok"] is False][0]
        campos, _ = procesar_mensaje(TOPIC, json.dumps(p).encode())
        self.assertFalse(campos["sensor_ok"])

    def test_confirmando_no_altera_el_estado(self):
        p = [p for p in firmware_payloads() if p["confirmando"]][0]
        campos, _ = procesar_mensaje(TOPIC, json.dumps(p).encode())
        self.assertTrue(campos["confirmando"])
        self.assertEqual(campos["estado_sistema"], "INCENDIO_MODERADO")
        self.assertLessEqual(len(campos["estado_sistema"]), 20)   # max_length actual del modelo

    def test_firmware_antiguo_sin_sensor_ok(self):
        p = dict(firmware_payloads()[0]); p.pop("sensor_ok")
        campos, _ = procesar_mensaje(TOPIC, json.dumps(p).encode())
        self.assertTrue(campos["sensor_ok"])


class Robustez(unittest.TestCase):
    base = None

    def setUp(self):
        self.base = dict(firmware_payloads()[0])

    def rechaza(self, topic, raw):
        campos, motivo = procesar_mensaje(topic, raw)   # no debe lanzar
        self.assertIsNone(campos, motivo)

    def test_malformados_se_rechazan_sin_excepcion(self):
        casos = [
            {**self.base, "temperatura": "abc"}, {**self.base, "prioridad": None},
            {**self.base, "prioridad": 10}, {**self.base, "prioridad": 2.5},
            {**self.base, "gas_raw": 99999}, {**self.base, "gas_raw": -1},
            {**self.base, "presion_raw": 4096}, {**self.base, "v1_pct": 101},
            {**self.base, "llama": 1}, {**self.base, "temperatura": True},
            {**self.base, "temperatura": 301}, {**self.base, "temperatura": -56},
            {**self.base, "delta_t": "x"}, {**self.base, "ts": -5},
            [1, 2], "texto", 42, None,
        ]
        for c in casos:
            self.rechaza(TOPIC, json.dumps(c).encode())

    def test_json_invalido_nan_y_binario(self):
        for raw in (b"", b"{", b"\xff\xfe\x00", b'{"temperatura": NaN}', b"[" * 5000 + b"]" * 5000):
            self.rechaza(TOPIC, raw)

    def test_campo_requerido_faltante(self):
        for k in ("temperatura", "gas_raw", "presion_raw", "llama", "prioridad", "v1_pct", "v2_pct", "v3_pct"):
            d = dict(self.base); d.pop(k)
            self.rechaza(TOPIC, json.dumps(d).encode())

    def test_payload_demasiado_grande(self):
        self.rechaza(TOPIC, json.dumps({**self.base, "relleno": "x" * 5000}).encode())

    def test_topics(self):
        raw = json.dumps(self.base).encode()
        for t in ("cocinas", "cocinas/", "cocinas/a/b", "otros/cocina_01", "cocinas/co cina", "cocinas/" + "a" * 33, ""):
            self.rechaza(t, raw)
        self.assertEqual(procesar_mensaje("cocinas/cocina_N-2", raw)[0]["cocina_id"], "cocina_N-2")

    def test_validar_payload_no_lanza(self):
        for x in (None, 1, "s", [], {}, {"temperatura": object()}):
            ok, _ = validar_payload(x)
            self.assertFalse(ok)


class Simulador(unittest.TestCase):
    ESPERADO = {  # tabla de mqtt/README.md
        "normal": {7}, "carga_alta": {7}, "prealerta_gas": {4}, "gas_critico": {1},
        "incendio_moderado": {3}, "incendio_severo": {2, 3}, "explosion_inminente": {0},
    }

    def test_el_simulador_cumple_el_contrato_y_las_prioridades_documentadas(self):
        random.seed(7)
        for nombre, prios in self.ESPERADO.items():
            c = mqtt_simulator.CocinaVirtual("cocina_x", nombre)
            vistos = set()
            for _ in range(60):
                l = c.siguiente_lectura()
                campos, motivo = procesar_mensaje("cocinas/cocina_x", json.dumps(l).encode())
                self.assertIsNotNone(campos, f"{nombre}: {motivo} {l}")
                vistos.add(l["prioridad"])
            self.assertTrue(vistos & prios, f"{nombre}: prioridades {vistos}, esperadas {prios}")

    def test_el_simulador_importa_sin_paho(self):
        self.assertNotIn("paho", sys.modules)


class CallbackOnMessage(unittest.TestCase):
    """mqtt_client.on_message con paho y la BD simuladas: nada debe detener el suscriptor."""

    def setUp(self):
        self._previos = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.client", "mqtt_client")}
        paho, mq, cl = types.ModuleType("paho"), types.ModuleType("paho.mqtt"), types.ModuleType("paho.mqtt.client")
        cl.Client = lambda *a, **k: types.SimpleNamespace()
        paho.mqtt, mq.client = mq, cl
        sys.modules.update({"paho": paho, "paho.mqtt": mq, "paho.mqtt.client": cl})
        sys.modules.pop("mqtt_client", None)
        import mqtt_client
        self.mc = mqtt_client
        self.guardadas = []
        mqtt_client.guardar_lectura = lambda campos: (self.guardadas.append(campos), types.SimpleNamespace(id=len(self.guardadas)))[1]

    def tearDown(self):
        for k, v in self._previos.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v

    def msg(self, topic, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return types.SimpleNamespace(topic=topic, payload=raw)

    def test_valido_se_guarda_y_malformado_no_detiene(self):
        good = firmware_payloads()[0]
        for m in (self.msg(TOPIC, {**good, "temperatura": "abc"}), self.msg(TOPIC, [1]), self.msg(TOPIC, b"\xff"),
                  self.msg("cocinas/a/b", good), self.msg(TOPIC, good)):
            self.mc.on_message(None, None, m)          # no debe lanzar
        self.assertEqual(len(self.guardadas), 1)

    def test_error_de_base_de_datos_no_detiene(self):
        def falla(campos): raise RuntimeError("connection already closed")
        self.mc.guardar_lectura = falla
        self.mc.on_message(None, None, self.msg(TOPIC, firmware_payloads()[0]))   # no debe lanzar

    def test_la_alarma_se_procesa_tras_guardar_y_su_fallo_no_detiene(self):
        procesadas = []
        self.mc.procesar_alarma = lambda l: procesadas.append(l.id)
        self.mc.on_message(None, None, self.msg(TOPIC, firmware_payloads()[1]))
        self.assertEqual(procesadas, [1])

        def falla(l): raise RuntimeError("alarma")
        self.mc.procesar_alarma = falla
        self.mc.on_message(None, None, self.msg(TOPIC, firmware_payloads()[1]))   # no debe lanzar
        self.assertEqual(len(self.guardadas), 2)                                  # la lectura sí se guardó

    def test_si_no_se_guarda_no_se_procesa_alarma(self):
        procesadas = []
        self.mc.procesar_alarma = lambda l: procesadas.append(l)
        self.mc.guardar_lectura = lambda campos: (_ for _ in ()).throw(RuntimeError("bd"))
        self.mc.on_message(None, None, self.msg(TOPIC, firmware_payloads()[1]))
        self.assertEqual(procesadas, [])


if __name__ == "__main__":
    unittest.main()
