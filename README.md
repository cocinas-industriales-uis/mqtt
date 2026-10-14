# 📡 MQTT — Sistema IoT Cocinas Industriales

Módulo de comunicación en tiempo real entre el ESP32, el broker Mosquitto y el backend Django.

---

## Arquitectura

```
ESP32 (publisher)
    │  publica JSON en cocinas/cocina_01
    ▼
Mosquitto (broker) ← localhost:1883
    │
    ├── mqtt_client.py     → valida (payload.py) y guarda lecturas en Django
    └── mqtt_simulator.py  → genera cocinas virtuales
```

---

## Estructura de topics

```
cocinas/
├── cocina_01     ← ESP32 físico
├── cocina_02     ← cocina virtual
├── cocina_03     ← cocina virtual
└── cocina_N      ← escalable sin cambiar el suscriptor
```

El suscriptor se suscribe a `cocinas/#` y recibe todas automáticamente.

---

## Formato del payload JSON

Cada mensaje publicado en el broker tiene este formato:

```json
{
  "temperatura":           25.3,
  "gas_raw":               450,
  "gas_pct":               11,
  "presion_raw":           1240,
  "presion_pct":           30,
  "llama":                 false,
  "delta_t":               0.3,
  "prioridad":             7,
  "v1_pct":                45,
  "v2_pct":                45,
  "v3_pct":                0,
  "valvula":               false,
  "aspersor":              false,
  "confirmando":           false,
  "sensor_ok":             true,
  "ts":                    183420
}
```

> `valvula=true` significa válvula **cerrada**. La temperatura válida es −55..300 °C; `gas_raw` y `presion_raw` 0..4095.
> Los mensajes que no cumplen tipos/rangos se **rechazan y se registran** sin detener el suscriptor (`payload.py`).

### Campos clave

| Campo | Tipo | Descripción |
|---|---|---|
| `temperatura` | float | °C — Steinhart-Hart en hardware real |
| `gas_raw` | int | ADC 0–4095 (ESP32) |
| `gas_pct` | int | % escala 0–100 |
| `llama` | bool | KY-026: `true` = llama detectada |
| `delta_t` | float | °C/s — velocidad de cambio de temperatura |
| `prioridad` | int | 0–9 según la lógica del firmware |
| `confirmando` | bool | `true` = condición detectada, aún sin confirmar |
| `sensor_ok` | bool | `false` = NTC averiado: `temperatura` (0) no es una medida. Opcional (firmware antiguo no lo envía → `true`) |
| `ts` | int | millis() desde arranque del ESP32 (**no es hora real**; la hora de la lectura es la de recepción en el servidor) |

---

## Prioridades y frecuencia de envío

| Prioridad | Condición | Intervalo MQTT |
|---|---|---|
| P0 | Gas + Llama simultáneos | **0.5s** |
| P1 | Gas > 20% LEL | **0.5s** |
| P2 | Llama + Temp > 90°C | **0.5s** |
| P3 | Llama + Temp 60–90°C / ΔT > 2°C/s | **0.5s** |
| P4 | Gas 10–20% LEL | **1s** |
| P5 | Presión alta + Gas alerta | **1s** |
| P6 | Presión alta | **5s** |
| P7 | Temp 20–60°C | **5s** |
| P8 | Llama sola estable | **5s** |
| Normal | Sin condición | **5s** |

---

## Instalación y uso

### 1. Instalar Mosquitto (broker)

```bash
sudo pacman -S mosquitto          # Arch Linux
sudo apt install mosquitto        # Ubuntu/Debian

sudo systemctl enable mosquitto
sudo systemctl start mosquitto

# Verificar que funciona
mosquitto_sub -t "prueba" -v &
mosquitto_pub -t "prueba" -m "hola"
# Debería mostrar: prueba hola
```

### 2. Instalar dependencias Python

```bash
cd mqtt/
pip install -r requirements.txt
```

### 3. Correr el suscriptor Django

Requiere el entorno del backend (Django y sus dependencias) activo. `SERVIDOR_DIR` apunta a la carpeta del repo
`servidor` (por defecto `../servidor`, es decir, los repos clonados uno junto al otro):

```bash
cd mqtt/
source ../servidor/venv/bin/activate        # o el entorno donde instaló el backend
SERVIDOR_DIR=../servidor python mqtt_client.py
```

Opcional: `MQTT_USER` / `MQTT_PASSWORD` si el broker exige autenticación (el firmware aún no la soporta).

> **Estado:** el modelo `Lectura` de `servidor/` todavía no tiene los campos de este contrato (Fase 3 de la auditoría);
> hasta entonces el suscriptor valida pero la escritura en BD fallará y quedará registrada en el log.

### 4. Probar con el simulador

```bash
# 3 cocinas con escenarios variados
python mqtt_simulator.py

# 10 cocinas simultáneas
python mqtt_simulator.py --cocinas 10

# Simular gas crítico en una cocina específica
python mqtt_simulator.py --escenario gas_critico --cocina cocina_02

# Simular incendio moderado
python mqtt_simulator.py --escenario incendio_moderado --cocina cocina_03

# Ver todos los escenarios disponibles
python mqtt_simulator.py --listar
```

### 5. Correr todo junto

```bash
# Terminal 1
sudo systemctl start mosquitto

# Terminal 2 — suscriptor
cd mqtt && source ../servidor/venv/bin/activate
SERVIDOR_DIR=../servidor python mqtt_client.py

# Terminal 3 — backend Django
cd servidor && python manage.py runserver

# Terminal 4 — simulador (opcional, sin hardware físico)
cd mqtt && python mqtt_simulator.py --cocinas 5

# Terminal 5 — frontend
cd pagina-web && npm run dev
```

---

## Escenarios del simulador

| Escenario | Descripción | Prioridad esperada |
|---|---|---|
| `normal` | Operación con carga moderada | P7 |
| `carga_alta` | Todos los fogones al máximo | P7 |
| `prealerta_gas` | Fuga leve de gas | P4 |
| `gas_critico` | Fuga crítica | P1 |
| `incendio_moderado` | Llama + temperatura 60–90°C | P3 |
| `incendio_severo` | Llama + temperatura > 90°C | P2 |
| `explosion_inminente` | Gas crítico + llama | P0 |

---

## Pruebas

```bash
# Sin broker, sin paho y sin Django: contrato con el firmware real, mensajes malformados, simulador
python -m unittest test_offline -v

# Con broker Mosquitto en marcha (prueba el broker y la red, no el código del proyecto)
python mqtt_test.py
```

`test_data/firmware_payloads.jsonl` contiene payloads generados por el firmware real (`esp32/test_host`, escenario `DUMP`).

## Variables de entorno

```bash
export MQTT_BROKER="192.168.1.X"   # IP del broker (default: localhost)
export MQTT_PORT="1883"             # Puerto (default: 1883)
export SERVIDOR_DIR="../servidor"   # Carpeta del backend Django (solo mqtt_client.py)
export MQTT_USER / MQTT_PASSWORD    # Opcional
```

---

## Integración con Smart Campus UIS

Para integrar con la plataforma Smart Campus, cada cocina publica
en un topic con su ID único. La plataforma puede suscribirse a
`cocinas/#` para recibir todas, o a `cocinas/cocina_N` para una sola.

La cocina se identifica por el topic (`cocinas/<id>`, solo letras, números, `_` y `-`, máx. 32): el firmware
no envía `cocina_id` en el payload; el suscriptor lo agrega al guardar la lectura.

---

*Proyecto de grado · UIS · 2025–2026 · Cesar Daniel Ávila Barbosa*
