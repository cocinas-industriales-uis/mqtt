"""
test_integracion.py — Integración real MQTT → BD → alarma → API, sin broker ni paho (se simula solo el transporte).

    python -m unittest test_integracion -v        (desde mqtt/, con las dependencias del servidor instaladas)

Usa payloads generados por el firmware real (test_data/firmware_payloads.jsonl), el on_message real de
mqtt_client.py, el modelo/serializadores/vistas reales de Django y una BD SQLite temporal. Se omite si Django no está.
"""
import json
import os
import sys
import tempfile
import types
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
SERVIDOR = os.path.abspath(os.getenv("SERVIDOR_DIR", os.path.join(AQUI, "..", "servidor")))
sys.path.insert(0, AQUI)

try:
    import django  # noqa: F401
    import rest_framework  # noqa: F401
    HAY_DJANGO = True
except ImportError:
    HAY_DJANGO = False


def payloads():
    with open(os.path.join(AQUI, "test_data", "firmware_payloads.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


@unittest.skipUnless(HAY_DJANGO, "Django/DRF no instalados")
class MqttABdYApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_NAME"] = os.path.join(cls.tmp.name, "e2e.sqlite3")
        os.environ.setdefault("SECRET_KEY", "clave-solo-para-pruebas-de-integracion")
        os.environ["SERVIDOR_DIR"] = SERVIDOR
        sys.path.insert(0, SERVIDOR)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
        cls.previos = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.client", "mqtt_client")}
        paho, mq, cl = types.ModuleType("paho"), types.ModuleType("paho.mqtt"), types.ModuleType("paho.mqtt.client")
        cl.Client = lambda *a, **k: types.SimpleNamespace()
        paho.mqtt, mq.client = mq, cl
        sys.modules.update({"paho": paho, "paho.mqtt": mq, "paho.mqtt.client": cl})
        sys.modules.pop("mqtt_client", None)
        import mqtt_client
        cls.mc = mqtt_client
        mqtt_client._cargar_django()
        from django.core.management import call_command
        from django.test.utils import setup_test_environment
        setup_test_environment()          # permite el host «testserver» del cliente de pruebas
        call_command("migrate", verbosity=0)
        from django.contrib.auth import get_user_model
        from django.contrib.auth.hashers import make_password
        from cocina.models import CodigoReconocimiento
        u = get_user_model().objects.create_user("operador", password="clave-segura-1", first_name="Ana", last_name="Ruiz")
        CodigoReconocimiento.objects.create(usuario=u, codigo_hash=make_password("4321"))

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.previos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        from cocina.models import Alarma, Lectura
        Alarma.objects.all().delete()
        Lectura.objects.all().delete()

    def publicar(self, cocina, payload):
        msg = types.SimpleNamespace(topic=f"cocinas/{cocina}", payload=json.dumps(payload).encode())
        self.mc.on_message(None, None, msg)

    def api(self):
        from rest_framework.test import APIClient
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": "operador", "password": "clave-segura-1"}, format="json")
        self.assertEqual(r.status_code, 200)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return c

    def test_todos_los_payloads_del_firmware_se_guardan_y_se_exponen(self):
        from cocina.models import Lectura
        ps = payloads()
        for p in ps:
            self.publicar("cocina_01", p)
        self.assertEqual(Lectura.objects.count(), len(ps))
        r = self.api().get("/api/lecturas/ultimas/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data[0]["cocina_id"], "cocina_01")

    def test_flujo_completo_fuga_de_gas_alarma_y_reconocimiento(self):
        from cocina.models import Alarma
        fuga = next(p for p in payloads() if p["prioridad"] == 1)
        normal = next(p for p in payloads() if p["prioridad"] in (7, 9))
        api = self.api()

        self.publicar("cocina_01", normal)
        self.assertEqual(api.get("/api/alarmas/").data, [])

        self.publicar("cocina_01", fuga)
        self.publicar("cocina_01", fuga)                      # repetida: no duplica
        lista = api.get("/api/alarmas/?estado=ACTIVA").data
        self.assertEqual(len(lista), 1)
        self.assertEqual(lista[0]["tipo"], "Fuga de gas crítica")
        self.assertEqual(lista[0]["cocina_id"], "cocina_01")

        self.publicar("cocina_01", normal)                    # la condición pasó: sigue ACTIVA
        self.assertEqual(Alarma.objects.get().estado, "ACTIVA")

        r = api.post(f"/api/alarmas/{lista[0]['id']}/reconocer/", {"codigo": "4321"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["estado"], "RECONOCIDA")
        self.assertEqual(r.data["reconocida_por"], "Ana Ruiz")
        self.assertEqual(api.get("/api/alarmas/?estado=ACTIVA").data, [])

    def test_p3_por_temperatura_en_confirmacion_no_alarma(self):
        from cocina.models import Alarma
        base = next(p for p in payloads() if p["prioridad"] in (7, 9))
        self.publicar("cocina_02", {**base, "prioridad": 3, "temperatura": 65.0, "llama": True, "confirmando": True})
        self.assertEqual(Alarma.objects.count(), 0)
        self.publicar("cocina_02", {**base, "prioridad": 3, "temperatura": 65.0, "llama": True, "confirmando": False})
        self.assertEqual(Alarma.objects.get().cocina_id, "cocina_02")

    def test_stream_entrega_lectura_y_alarma_y_mide_latencia(self):
        """MQTT → BD → stream: la lectura y la alarma salen por SSE en el primer sondeo tras publicarse.
        La latencia total real = tiempo de procesar el mensaje (medido aquí) + hasta STREAM_POLL_S de sondeo."""
        import json as _json
        import time
        from django.conf import settings
        from cocina import stream
        fuga = next(p for p in payloads() if p["prioridad"] == 1)
        estado = stream.Estado()
        t0 = time.perf_counter()
        self.publicar("cocina_01", fuga)
        t_proceso = time.perf_counter() - t0
        eventos = stream.sondear(estado)
        t_total = time.perf_counter() - t0
        tipos = [e.split("\n", 1)[0] for e in eventos]
        self.assertEqual(tipos, ["event: reading", "event: alarm"])
        lectura = _json.loads(eventos[0].split("data: ", 1)[1])
        self.assertEqual(lectura["cocina_id"], "cocina_01")
        self.assertEqual(_json.loads(eventos[1].split("data: ", 1)[1])["estado"], "ACTIVA")
        self.assertLess(t_total, 0.5)
        print(f"\n[latencia] procesar mensaje (BD+alarma): {t_proceso*1000:.1f} ms; "
              f"hasta el evento SSE: {t_total*1000:.1f} ms; peor caso por sondeo: +{settings.STREAM_POLL_S*1000:.0f} ms")


if __name__ == "__main__":
    unittest.main()
