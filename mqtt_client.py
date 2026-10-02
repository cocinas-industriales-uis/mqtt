# Backend_cocina/cocina/mqtt_client.py

import paho.mqtt.client as mqtt
import json
import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from cocina.models import Lectura

BROKER = "localhost"
PORT   = 1883
TOPIC  = "cocinas/#"   # escucha todas las cocinas

def on_connect(client, userdata, flags, rc):
    print(f"Conectado al broker MQTT (código {rc})")
    client.subscribe(TOPIC)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        print(f"Mensaje recibido en {msg.topic}: {payload}")

        # Guarda en la base de datos igual que antes
        Lectura.objects.create(
            temperatura          = payload.get("temperatura", 0),
            nivel_gas            = payload.get("nivel_gas", 0),
            presion              = payload.get("presion", 0),
            llama_detectada      = payload.get("llama_detectada", False),
            ventilador_extraccion  = payload.get("ventilador_extraccion", False),
            ventilador_inyeccion_1 = payload.get("ventilador_inyeccion_1", False),
            ventilador_inyeccion_2 = payload.get("ventilador_inyeccion_2", False),
            estado_sistema       = payload.get("estado_sistema", "NORMAL"),
        )
        print("Lectura guardada en base de datos")

    except Exception as e:
        print(f"Error procesando mensaje: {e}")

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)
client.loop_forever()   # bloquea — corre en su propio proceso