"""
Configuración central del bot.
Editá los valores bajo PERFIL_FINANCIERO y TASA_CAMBIO según cambien.
Las credenciales se leen desde variables de entorno (.env / Replit Secrets).
"""
import os
from pathlib import Path
from datetime import date

# ----- Credenciales -----
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# Provider de IA: "gemini" (free tier) o "anthropic" (paid).
AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()

# API keys de cada provider. Solo necesitás la que vayas a usar.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Multi-usuario: lista separada por comas. Vacío = bot abierto (NO recomendado).
# Ejemplo: ALLOWED_USER_IDS=12345678,87654321
_raw = os.environ.get("ALLOWED_USER_IDS", "") or os.environ.get("ALLOWED_USER_ID", "")
ALLOWED_USER_IDS: set[int] = {
    int(x.strip()) for x in _raw.split(",") if x.strip().isdigit()
}

# Admin user (vos) — único que puede usar /admin
ADMIN_USER_ID = 6568261984

# Master key para cifrar el JSON. Si está seteada, los datos se guardan
# cifrados con AES (Fernet). Sin esta key, los datos no se pueden leer.
# Generá una con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MASTER_KEY = os.environ.get("MASTER_KEY", "")

# ----- Almacenamiento -----
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(exist_ok=True)
DATA_FILE = DATA_DIR / ("gastos.enc" if MASTER_KEY else "gastos.json")

# ----- Modelos según provider -----
# FAST: para categorización (1 llamada por gasto). SMART: reportes y decisiones.
if AI_PROVIDER == "gemini":
    # Free tier: Flash 10 RPM/250 RPD, Pro 5 RPM/100 RPD (a abril 2026).
    # Para uso personal sobra holgado. Si te quedan cortas, cambiá a "flash-lite".
    MODEL_FAST = os.environ.get("MODEL_FAST", "gemini-2.5-flash")
    MODEL_SMART = os.environ.get("MODEL_SMART", "gemini-2.5-pro")
elif AI_PROVIDER == "anthropic":
    MODEL_FAST = os.environ.get("MODEL_FAST", "claude-haiku-4-5")
    MODEL_SMART = os.environ.get("MODEL_SMART", "claude-sonnet-4-6")
else:
    # Sin provider configurado: el bot funciona pero los reportes/categorización
    # caen al fallback. Útil para testing.
    MODEL_FAST = ""
    MODEL_SMART = ""

# ----- Monedas -----
MONEDAS = ["ARS", "USD", "USDT", "EUR"]
MONEDA_PRINCIPAL = "ARS"

# Tipo de cambio para convertir a la moneda principal en reportes.
# Editá cuando cambien (blue/MEP/CCL).
TASA_CAMBIO = {
    "ARS": 1,
    "USD": 1300,
    "USDT": 1290,
    "EUR": 1400,
}

# ----- Formas de pago -----
FORMAS_PAGO = ["credito", "debito", "efectivo", "otro"]
FORMA_PAGO_LABELS = {
    "credito": "💳 Crédito",
    "debito": "💸 Débito",
    "efectivo": "💵 Efectivo",
    "otro": "🪙 Otro",
}

# ----- Scheduler -----
DIA_PROMPT_MENSUAL = 1
HORA_PROMPT_MENSUAL = 10
MINUTO_PROMPT_MENSUAL = 0

# Recordatorio nocturno: pregunta si tuviste gastos.
HORA_PROMPT_NOCTURNO = 22
MINUTO_PROMPT_NOCTURNO = 0

TIMEZONE = "America/Argentina/Buenos_Aires"

# ----- Perfil financiero -----
# NOTA MULTI-USUARIO: cuando habilites más personas, mové este bloque a un
# dict por usuario en storage (cada uno setea el suyo con un /setup). Por
# ahora vale para vos; los otros usuarios verán números genéricos hasta
# que setees per-user.
PERFIL_FINANCIERO = {
    "moneda_principal": MONEDA_PRINCIPAL,
    "ingreso_mensual": 2_968_000,
    "presupuesto_mes_actual": 950_000,
    "objetivo_innecesario_pct": 15,
    "deudas": [
        {
            "nombre": "Santander",
            "saldo": 38_000_000,
            "cuota": 1_700_000,
            "vencimiento_dia": 6,
            "nota": "Refinanciación en julio",
        },
        {
            "nombre": "Galicia",
            "saldo_aprox": 12_500_000,
            "cuota_aprox": 2_468_450,
            "vencimiento_dia": 8,
            "nota": "Pago agresivo en junio",
        },
    ],
    "plan": (
        "Mayo: control estricto (max 950k). Junio: ataque agresivo a tarjeta "
        "tras venta de camioneta (~10k USD)."
    ),
    "fecha_inicio_mes": date.today().replace(day=1).isoformat(),
}

TARJETAS_RESUMEN_MENSUAL = [d["nombre"] for d in PERFIL_FINANCIERO["deudas"]]

# ----- Categorías -----
CATEGORIAS = [
    "Alimentación", "Transporte", "Vivienda", "Servicios", "Salud",
    "Entretenimiento", "Suscripciones", "Compras", "Educación",
    "Viajes", "Deuda", "Impuestos", "Otros",
]
NECESIDADES = ["necesario", "importante", "innecesario"]
URGENCIAS = ["alta", "media", "baja"]
TIPOS = ["fijo", "variable"]

# ----- Umbrales de alertas -----
GASTO_GRANDE_PCT = 0.50
DESVIO_PROYECCION_PCT = 0.10


def autorizado(user_id: int) -> bool:
    """True si el usuario puede usar el bot."""
    if not ALLOWED_USER_IDS:
        return True  # bot abierto (modo dev)
    return user_id in ALLOWED_USER_IDS


def presupuesto_diario() -> int:
    return PERFIL_FINANCIERO["presupuesto_mes_actual"] // 30


def convertir_a_principal(monto: float, moneda: str) -> float:
    if moneda == MONEDA_PRINCIPAL:
        return float(monto)
    return float(monto) * TASA_CAMBIO.get(moneda, 1)
