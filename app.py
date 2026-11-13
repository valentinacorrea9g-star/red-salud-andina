import os
import re
import csv
import datetime
from pathlib import Path

import requests
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="Asistente de Red Salud Andina", page_icon="🏥")

BASE = Path(__file__).parent
LOG_LOCAL = BASE / "registro_local.csv"  # respaldo; el registro principal va a Sheets


# --- Configuración: primero st.secrets (Streamlit Cloud), luego variables de entorno (local) ---
def secreto(nombre: str, defecto: str = "") -> str:
    try:
        if nombre in st.secrets:
            return str(st.secrets[nombre])
    except Exception:
        pass
    return os.environ.get(nombre, defecto)


MODEL = secreto("GEMINI_MODEL", "gemini-2.5-flash")
SHEETS_WEBHOOK = secreto("SHEETS_WEBHOOK")


@st.cache_resource
def cargar_sistema() -> str:
    prompt = (BASE / "prompt" / "sistema.md").read_text(encoding="utf-8")
    conocimiento = "\n\n".join(
        f"## Archivo: {f.name}\n{f.read_text(encoding='utf-8')}"
        for f in sorted((BASE / "base_conocimiento").glob("*.md"))
    )
    return prompt + "\n\n# BASE DE CONOCIMIENTO (única fuente)\n" + conocimiento


@st.cache_resource
def cliente_gemini():
    clave = secreto("GEMINI_API_KEY")
    if not clave:
        return None
    return genai.Client(api_key=clave)


# --- Reglas en código, ANTES del modelo ---
URGENCIA = re.compile(
    r"urgenc|emergenc|dolor (fuerte )?de pecho|sangr|no (puede|puedo) respirar|"
    r"desmay|convulsi|infarto|derrame",
    re.IGNORECASE,
)
# Cédulas (6 a 11 dígitos) y celulares colombianos (3xx xxx xxxx, con o sin +57)
DATO_PERSONAL = re.compile(r"\b\d{6,11}\b|(\+?57\s?)?\b3\d{2}[\s-]?\d{3}[\s-]?\d{4}\b")

RESP_URGENCIA = (
    "Si es una emergencia, llama al 123 ahora o ve al servicio de urgencias más cercano. "
    "Red Salud Andina no atiende urgencias."
)
RESP_DATO = (
    "Por tu seguridad no escribas documentos, teléfonos ni datos personales aquí, "
    "y no los guardo. Cuéntame tu pregunta sin ellos y te oriento."
)
RESP_ERROR = (
    "En este momento no puedo responder. Intenta de nuevo o escribe al "
    "call center: (601) 555 0100, L–V de 7:00 a 19:00 y sábados de 8:00 a 14:00."
)

TAG = re.compile(r"\[\[ESTADO:\s*(\w+)\s*\|\s*CANAL:\s*(\w+)\s*\]\]")


def sanear(texto: str) -> str:
    """Solo se guarda la pregunta. Si parece tener un dato personal, se descarta."""
    if not texto:
        return ""
    if DATO_PERSONAL.search(texto):
        return "[descartada: posible dato personal]"
    return texto[:200]


def registrar(evento: str, pregunta: str = "", canal: str = "ninguno", turnos: int = 0):
    fila = {
        "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
        "evento": evento,
        "turnos": turnos,
        "canal_ofrecido": canal,
        "pregunta": sanear(pregunta),
    }
    if SHEETS_WEBHOOK:
        try:
            requests.post(SHEETS_WEBHOOK, json=fila, timeout=5)
        except requests.RequestException:
            pass  # el registro nunca debe tumbar la conversación
    try:
        nuevo = not LOG_LOCAL.exists()
        with LOG_LOCAL.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(fila.keys()))
            if nuevo:
                w.writeheader()
            w.writerow(fila)
    except OSError:
        pass  # algunos hosts no permiten escribir en disco


def responder(mensaje: str, historial: list[dict]) -> str:
    turnos = sum(1 for m in historial if m["role"] == "user") + 1

    if URGENCIA.search(mensaje):
        registrar("urgencia", turnos=turnos, canal="ninguno")
        return RESP_URGENCIA

    if DATO_PERSONAL.search(mensaje):
        return RESP_DATO

    cliente = cliente_gemini()
    if cliente is None:
        return RESP_ERROR

    contents = []
    for m in historial:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=mensaje)]))

    try:
        resp = cliente.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=cargar_sistema(),
                temperature=0.2,
            ),
        )
        texto = resp.text or ""
    except Exception as e:
        print(f"[GEMINI] ERROR: {type(e).__name__}: {e}", flush=True)
        return RESP_ERROR

    etiqueta = TAG.search(texto)
    visible = TAG.sub("", texto).strip()

    if etiqueta:
        estado, canal = etiqueta.group(1), etiqueta.group(2)
        if estado != "en_curso":
            registrar(
                estado,
                pregunta=mensaje if estado == "sin_respuesta" else "",
                canal=canal,
                turnos=turnos,
            )

    return visible or RESP_ERROR


# --- Interfaz ---
st.title("Asistente de Red Salud Andina (ficticia)")
st.caption(
    "Informo sobre sedes, horarios, requisitos, cuotas y canales. "
    "No agendo citas, no consulto resultados y no doy consejo médico."
)

if not secreto("GEMINI_API_KEY"):
    st.error("Falta configurar el secret GEMINI_API_KEY en la configuración de la app.")
    st.stop()

if "mensajes" not in st.session_state:
    st.session_state.mensajes = []

if not st.session_state.mensajes:
    st.markdown(
        "Puedes preguntar, por ejemplo:\n"
        "- ¿Qué debo llevar a mi cita de ortopedia?\n"
        "- ¿Me atienden un domingo en la noche por WhatsApp?\n"
        "- ¿Cuánto cuesta la consulta de pediatría?\n"
        "- ¿Cuándo salen mis resultados de laboratorio?"
    )

for m in st.session_state.mensajes:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

mensaje = st.chat_input("Escribe tu pregunta")

if mensaje:
    historial_previo = list(st.session_state.mensajes)
    st.session_state.mensajes.append({"role": "user", "content": mensaje})
    with st.chat_message("user"):
        st.markdown(mensaje)

    with st.chat_message("assistant"):
        with st.spinner("Revisando la información..."):
            respuesta = responder(mensaje, historial_previo)
        st.markdown(respuesta)

    st.session_state.mensajes.append({"role": "assistant", "content": respuesta})