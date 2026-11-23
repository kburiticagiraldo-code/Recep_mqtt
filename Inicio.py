import hashlib
import html
import json
import random
import time
from collections import deque

import paho.mqtt.client as mqtt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Monitor de Invernadero", page_icon="🌱", layout="centered")

# --- lógica ---------------------------------------------------------------
PLANTAS = {
    "Hortalizas (tomate, lechuga)": ((18, 27), (50, 70)),
    "Helecho / planta tropical": ((18, 26), (60, 85)),
    "Orquídea": ((18, 27), (50, 75)),
    "Suculenta / cactus": ((18, 30), (20, 50)),
}

ESTADO_TXT = {"ok": "Ideal", "bajo": "Bajo", "alto": "Alto"}

PROBLEMAS = {
    ("temp", "alto"): ("🥵", "Hace demasiado calor", "Muévela a la sombra o ventila el invernadero."),
    ("temp", "bajo"): ("🥶", "Hace frío", "Acércala a más luz y protégela de las corrientes de aire."),
    ("hum", "bajo"): ("🏜️", "El ambiente está seco", "Riega o sube la humedad con un atomizador."),
    ("hum", "alto"): ("💦", "Demasiada humedad", "Ventila para evitar hongos y exceso de agua."),
}


def clasificar(clave):
    k = str(clave).lower()
    if k in ("t", "temp", "temperatura", "temperature") or "temp" in k:
        return "temp"
    if k in ("h", "hum", "humedad", "humidity") or "hum" in k:
        return "hum"
    if k in ("p", "pres", "presion", "presión", "pressure") or "pres" in k:
        return "pres"
    return None


def extraer(payload):
    """Devuelve {'temp':..., 'hum':..., 'pres':..., 'otros': {...}} a partir del JSON recibido."""
    res = {"otros": {}}
    if not isinstance(payload, dict):
        return res
    for k, v in payload.items():
        tipo = clasificar(k)
        try:
            num = float(v)
        except (TypeError, ValueError):
            res["otros"][k] = v
            continue
        if tipo and tipo not in res:
            res[tipo] = num
        else:
            res["otros"][k] = v
    return res


def estado_de(valor, lo, hi):
    if valor < lo:
        return "bajo"
    if valor > hi:
        return "alto"
    return "ok"


def resumen(estados):
    """estados: {'temp': 'ok'|'bajo'|'alto', 'hum': ...} -> (emoji, titulo, consejo, hay_problema)"""
    problemas = [PROBLEMAS[(m, e)] for m, e in estados.items() if (m, e) in PROBLEMAS]
    if not problemas:
        return "🌿", "¡Tu planta está feliz!", "Todo está dentro del rango ideal.", False
    emoji, titulo, _ = problemas[0]
    consejo = " ".join(p[2] for p in problemas)
    return emoji, titulo, consejo, True


def tarjeta_metrica(icono, nombre, valor, unidad, est, lo, hi, vmin, vmax):
    rango = vmax - vmin
    pos = min(max((valor - vmin) / rango * 100, 0), 100)
    a = min(max((lo - vmin) / rango * 100, 0), 100)
    b = min(max((hi - vmin) / rango * 100, 0), 100)
    return "".join([
        '<div class="mcard">',
        f'<div class="mtop"><span>{icono}</span><span>{nombre}</span>'
        f'<span class="pill {est}">{ESTADO_TXT[est]}</span></div>',
        f'<div class="mval">{valor:.1f}<small>{unidad}</small></div>',
        f'<div class="rango"><div class="zona" style="left:{a:.0f}%;width:{b - a:.0f}%"></div>',
        f'<div class="marca {est}" style="left:{pos:.0f}%"></div></div>',
        f'<div class="rtxt"><span>{vmin}</span><span>ideal {lo}–{hi}</span><span>{vmax}</span></div>',
        '</div>',
    ])


def tarjeta_simple(icono, nombre, valor, unidad=""):
    return "".join([
        '<div class="mcard">',
        f'<div class="mtop"><span>{icono}</span><span>{html.escape(str(nombre))}</span></div>',
        f'<div class="mval">{html.escape(str(valor))}<small>{unidad}</small></div>',
        '</div>',
    ])


def nuevo_estado():
    return {"payload": None, "ts": None, "historial": deque(maxlen=60), "conectado": False}
# --- fin lógica -----------------------------------------------------------

CSS = """
<style>
.stApp{background:linear-gradient(180deg,#f0fdf4 0%,#fefce8 100%);}
[data-testid="stHeader"]{background:transparent;}
footer{visibility:hidden;}
.block-container{max-width:620px;padding-top:2rem;}
.hero{text-align:center;margin-bottom:14px;}
.badge{display:inline-block;padding:5px 14px;border-radius:999px;font-size:.75rem;font-weight:700;
 letter-spacing:.08em;color:#166534;background:#dcfce7;border:1px solid #86efac;}
.titulo{font-size:2.2rem;font-weight:800;margin:10px 0 2px;color:#14532d;line-height:1.1;}
.sub{color:#4d7c0f;font-size:.95rem;}
.estado-barra{display:flex;justify-content:center;flex-wrap:wrap;gap:10px;margin:8px 0 14px;}
.chip{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:999px;
 font-size:.78rem;font-weight:600;background:#fff;color:#475569;border:1px solid #e2e8f0;}
.chip i{width:8px;height:8px;border-radius:50%;background:#94a3b8;}
.chip.on i{background:#22c55e;box-shadow:0 0 8px #22c55e;}
.mood{text-align:center;padding:22px 18px;border-radius:26px;background:#fff;border:1px solid #bbf7d0;
 box-shadow:0 10px 30px rgba(20,83,45,.10);}
.mood.warn{background:#fffbeb;border-color:#fde68a;}
.mood .emoji{font-size:3.6rem;line-height:1.1;}
.mood .tit{font-size:1.4rem;font-weight:800;color:#14532d;margin-top:4px;}
.mood.warn .tit{color:#92400e;}
.mood .tip{color:#4b5563;font-size:.92rem;margin-top:4px;}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:16px 0;}
.mcard{background:#fff;border-radius:22px;padding:16px 16px 14px;border:1px solid #ecfccb;
 box-shadow:0 6px 20px rgba(20,83,45,.08);}
.mtop{display:flex;align-items:center;gap:8px;font-size:.85rem;font-weight:600;color:#475569;}
.pill{margin-left:auto;padding:2px 10px;border-radius:999px;font-size:.7rem;font-weight:700;}
.pill.ok{background:#dcfce7;color:#166534;} .pill.bajo{background:#e0f2fe;color:#075985;}
.pill.alto{background:#ffedd5;color:#9a3412;}
.mval{font-size:2.4rem;font-weight:800;color:#14532d;margin:6px 0 12px;}
.mval small{font-size:1rem;color:#64748b;margin-left:3px;font-weight:600;}
.rango{position:relative;height:10px;border-radius:999px;background:#e5e7eb;}
.zona{position:absolute;top:0;height:100%;border-radius:999px;background:#86efac;}
.marca{position:absolute;top:-4px;width:18px;height:18px;margin-left:-9px;border-radius:50%;
 background:#15803d;border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3);}
.marca.bajo{background:#0284c7;} .marca.alto{background:#ea580c;}
.rtxt{display:flex;justify-content:space-between;font-size:.7rem;color:#94a3b8;margin-top:9px;}
.espera{text-align:center;padding:30px 18px;border-radius:26px;background:#fff;border:1px dashed #86efac;
 color:#4b5563;}
.espera .emoji{font-size:3rem;}
.pie{text-align:center;color:#94a3b8;font-size:.75rem;margin-top:14px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def crear_cliente(client_id):
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


@st.cache_resource
def conectar(broker, port, topic, client_id):
    """Una sola conexión que se queda escuchando y guarda la última lectura."""
    estado = nuevo_estado()

    def on_connect(client, userdata, flags, rc, properties=None):
        estado["conectado"] = (rc == 0) or (hasattr(rc, "is_failure") and not rc.is_failure)
        client.subscribe(topic)

    def on_disconnect(client, userdata, *args):
        estado["conectado"] = False

    def on_message(client, userdata, message):
        texto = message.payload.decode(errors="ignore")
        try:
            payload = json.loads(texto)
        except ValueError:
            payload = texto
        estado["payload"] = payload
        estado["ts"] = time.time()
        estado["historial"].append(payload)

    client = crear_cliente(client_id)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(1, 10)
    client.connect_async(broker, port, 60)
    client.loop_start()
    return estado


def estado_demo():
    """Lecturas simuladas para probar la interfaz sin sensor."""
    if "demo" not in st.session_state:
        st.session_state.demo = nuevo_estado()
        st.session_state.demo["conectado"] = True
    demo = st.session_state.demo
    payload = {"Temp": round(random.uniform(14, 36), 1), "Hum": round(random.uniform(25, 92), 1),
               "Pres": round(random.uniform(1008, 1020), 1)}
    demo["payload"], demo["ts"] = payload, time.time()
    demo["historial"].append(payload)
    return demo


@st.fragment(run_every=2)
def panel(broker_estado, ideal, demo):
    estado = estado_demo() if demo else broker_estado

    ago = None if estado["ts"] is None else int(time.time() - estado["ts"])
    chip_con = f'<span class="chip {"on" if estado["conectado"] else ""}"><i></i>' \
               f'{"Modo demo" if demo else ("Conectado" if estado["conectado"] else "Sin conexión")}</span>'
    chip_ago = f'<span class="chip"><i></i>{"Esperando datos" if ago is None else f"Última lectura hace {ago} s"}</span>'
    st.markdown(f'<div class="estado-barra">{chip_con}{chip_ago}</div>', unsafe_allow_html=True)

    if estado["payload"] is None:
        st.markdown(
            '<div class="espera"><div class="emoji">🌱</div><b>Esperando datos del sensor…</b><br>'
            'Revisa el broker y el tópico en “Conexión MQTT”, y que tu dispositivo esté publicando.</div>',
            unsafe_allow_html=True,
        )
        return

    datos = extraer(estado["payload"])
    if not isinstance(estado["payload"], dict):
        st.markdown('<div class="mcard"><div class="mtop">Mensaje recibido</div></div>', unsafe_allow_html=True)
        st.code(str(estado["payload"]))
        return

    estados, tarjetas = {}, []
    if "temp" in datos:
        lo, hi = ideal["temp"]
        estados["temp"] = estado_de(datos["temp"], lo, hi)
        tarjetas.append(tarjeta_metrica("🌡️", "Temperatura", datos["temp"], "°C", estados["temp"], lo, hi, 0, 50))
    if "hum" in datos:
        lo, hi = ideal["hum"]
        estados["hum"] = estado_de(datos["hum"], lo, hi)
        tarjetas.append(tarjeta_metrica("💧", "Humedad", datos["hum"], "%", estados["hum"], lo, hi, 0, 100))
    if "pres" in datos:
        presion = datos["pres"] / 100 if datos["pres"] > 5000 else datos["pres"]  # Pa -> hPa
        tarjetas.append(tarjeta_simple("🧭", "Presión", f"{presion:.1f}", " hPa"))
    for clave, valor in datos["otros"].items():
        tarjetas.append(tarjeta_simple("📈", clave, valor))

    emoji, titulo, consejo, alerta = resumen(estados)
    st.markdown(
        f'<div class="mood {"warn" if alerta else ""}"><div class="emoji">{emoji}</div>'
        f'<div class="tit">{titulo}</div><div class="tip">{consejo}</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="grid">{"".join(tarjetas)}</div>', unsafe_allow_html=True)

    filas = []
    for p in list(estado["historial"]):
        d = extraer(p)
        fila = {}
        if "temp" in d:
            fila["Temperatura (°C)"] = d["temp"]
        if "hum" in d:
            fila["Humedad (%)"] = d["hum"]
        if fila:
            filas.append(fila)
    if len(filas) >= 2:
        st.markdown("**📊 Últimas lecturas**")
        st.line_chart(pd.DataFrame(filas), height=190)


# ---------- Interfaz ----------
st.markdown(
    '<div class="hero"><span class="badge">🌱 INVERNADERO · MQTT</span>'
    '<div class="titulo">Monitor de Invernadero</div>'
    '<div class="sub">Mira cómo está el ambiente de tu planta en tiempo real</div></div>',
    unsafe_allow_html=True,
)

planta = st.selectbox("¿Qué planta cuidas?", list(PLANTAS) + ["Personalizada"])
if planta == "Personalizada":
    t_rango = st.slider("Temperatura ideal (°C)", 0, 50, (18, 28))
    h_rango = st.slider("Humedad ideal (%)", 0, 100, (50, 80))
else:
    t_rango, h_rango = PLANTAS[planta]
ideal = {"temp": t_rango, "hum": h_rango}

panel_area = st.container()
cfg_area = st.container()

with cfg_area:
    with st.expander("⚙️ Conexión MQTT"):
        broker = st.text_input("Broker MQTT", value="broker.mqttdashboard.com")
        port = st.number_input("Puerto", value=1883, min_value=1, max_value=65535)
        topic = st.text_input("Tópico", value="Sensor/THP2")
        client_id = st.text_input("ID del cliente", value="streamlit_client")
        demo = st.checkbox("Modo demo (datos simulados, sin sensor)")
        st.caption("Rangos ideales: valores de referencia generales, ajústalos a tu planta.")

# El id incluye un sufijo según broker y tópico para que dos conexiones no se desconecten entre sí
sufijo = hashlib.md5(f"{broker}{topic}".encode()).hexdigest()[:4]
broker_estado = conectar(broker, int(port), topic, f"{client_id}-{sufijo}")

with panel_area:
    panel(broker_estado, ideal, demo)
