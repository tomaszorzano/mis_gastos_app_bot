"""
Cliente de IA unificado.
Soporta dos backends, controlado por config.AI_PROVIDER:
  - "gemini"   → Google Gemini API (free tier)
  - "anthropic"→ Claude API (paid)

Funciones públicas (mismo contrato sin importar backend):
  - clasificar_gasto(monto, descripcion) → dict
  - analizar_reporte(tipo, datos) → str
  - evaluar_decision(consulta, datos) → str

Si el backend está mal configurado o falla, devuelve fallback robusto para
que el bot nunca se trabe.
"""
import json
import logging
from typing import Optional

from config import (
    AI_PROVIDER,
    GEMINI_API_KEY,
    ANTHROPIC_API_KEY,
    MODEL_FAST,
    MODEL_SMART,
    CATEGORIAS,
    NECESIDADES,
    URGENCIAS,
    TIPOS,
    PERFIL_FINANCIERO,
)

log = logging.getLogger(__name__)

_gemini_client = None
_anthropic_client = None


# ----------------------------------------------------------------- #
# Clientes lazy                                                     #
# ----------------------------------------------------------------- #

def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("Falta GEMINI_API_KEY en variables de entorno")
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("Falta ANTHROPIC_API_KEY en variables de entorno")
        from anthropic import Anthropic
        _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


# ----------------------------------------------------------------- #
# Llamada genérica al modelo                                        #
# ----------------------------------------------------------------- #

def _llamar_modelo(modelo: str, system: str, user: str,
                   max_tokens: int = 800) -> str:
    """
    Hace una llamada al backend configurado y devuelve el texto plano.
    Si el backend falla, propaga la excepción (los callers la atrapan y
    devuelven fallback amigable).
    """
    if AI_PROVIDER == "gemini":
        from google.genai import types
        client = _get_gemini()
        resp = client.models.generate_content(
            model=modelo,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                temperature=0.4,
            ),
        )
        return (resp.text or "").strip()
    elif AI_PROVIDER == "anthropic":
        client = _get_anthropic()
        msg = client.messages.create(
            model=modelo,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    else:
        raise RuntimeError(f"AI_PROVIDER desconocido: {AI_PROVIDER!r}")


# ----------------------------------------------------------------- #
# Categorización                                                    #
# ----------------------------------------------------------------- #

_PROMPT_CATEGORIZAR = f"""Clasificás gastos personales en Argentina (pesos ARS).

Devolvé SIEMPRE JSON válido, sin texto extra, sin markdown, con exactamente estas claves:
{{
  "categoria": una de {CATEGORIAS},
  "tipo": una de {TIPOS},
  "necesidad": una de {NECESIDADES},
  "urgencia": una de {URGENCIAS}
}}

Guías:
- "necesario": comida básica, transporte al trabajo, servicios, salud, deuda.
- "importante": educación, ropa de uso, herramientas de trabajo.
- "innecesario": entretenimiento, suscripciones opcionales, lujos, antojos.
- "fijo": recurrente predecible (alquiler, Netflix, prepaga).
- "variable": puntual o que cambia mes a mes (super, nafta, salida).
- En duda → "Otros", "importante", "media", "variable".
"""


def _fallback_categorizacion() -> dict:
    return {
        "categoria": "Otros",
        "tipo": "variable",
        "necesidad": "importante",
        "urgencia": "media",
    }


def _validar_categorizacion(raw: dict) -> dict:
    fb = _fallback_categorizacion()
    return {
        "categoria": raw.get("categoria") if raw.get("categoria") in CATEGORIAS else fb["categoria"],
        "tipo": raw.get("tipo") if raw.get("tipo") in TIPOS else fb["tipo"],
        "necesidad": raw.get("necesidad") if raw.get("necesidad") in NECESIDADES else fb["necesidad"],
        "urgencia": raw.get("urgencia") if raw.get("urgencia") in URGENCIAS else fb["urgencia"],
    }


def _extraer_json(texto: str) -> Optional[dict]:
    """
    Algunos modelos envuelven el JSON en ```json ... ```. Lo limpiamos.
    Si todavía hay basura, intentamos encontrar el primer { y el último }.
    """
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j+1])
            except json.JSONDecodeError:
                return None
        return None


def clasificar_gasto(monto: float, descripcion: str) -> dict:
    """Clasifica con el modelo rápido. Falla seguro al fallback."""
    try:
        texto = _llamar_modelo(
            modelo=MODEL_FAST,
            system=_PROMPT_CATEGORIZAR,
            user=f"Monto: {monto} ARS\nDescripción: {descripcion}\n\nDevolvé SOLO el JSON.",
            max_tokens=200,
        )
        data = _extraer_json(texto)
        if not data:
            log.warning("clasificar_gasto: no pude parsear JSON: %r", texto[:200])
            return _fallback_categorizacion()
        return _validar_categorizacion(data)
    except Exception as e:
        log.warning("clasificar_gasto falló: %s", e)
        return _fallback_categorizacion()


# ----------------------------------------------------------------- #
# Análisis profundo                                                 #
# ----------------------------------------------------------------- #

# ----------------------------------------------------------------- #
# Análisis profundo                                                 #
# ----------------------------------------------------------------- #

def _contexto_financiero(usuario_id: int = None) -> str:
    """
    Construye el contexto del usuario. Si hay user_id y tiene perfil
    configurado, usa el suyo. Si no, usa el PERFIL_FINANCIERO global.
    """
    # Lazy import para evitar circular
    perfil = None
    if usuario_id is not None:
        try:
            import storage
            p_user = storage.obtener_perfil(usuario_id)
            if p_user.get("configurado"):
                perfil = p_user
        except Exception as e:
            log.warning("No pude leer perfil del usuario %s: %s", usuario_id, e)

    # Fallback al perfil global de config (compatibilidad v1)
    if perfil is None:
        p = PERFIL_FINANCIERO
        deudas_txt = "\n".join(
            f"  - {d['nombre']}: saldo ~{d.get('saldo', d.get('saldo_aprox', 0)):,} ARS, "
            f"cuota {d.get('cuota', d.get('cuota_aprox', 0)):,}, vence día {d['vencimiento_dia']}. {d['nota']}"
            for d in p["deudas"]
        )
        return f"""CONTEXTO DEL USUARIO (no inventar nada que no esté acá):
- Ingreso mensual: {p['ingreso_mensual']:,} ARS
- Presupuesto del mes: {p['presupuesto_mes_actual']:,} ARS (techo)
- Objetivo de gasto innecesario: <{p['objetivo_innecesario_pct']}% del presupuesto
- Deudas:
{deudas_txt}
- Plan: {p['plan']}
"""

    # Perfil del usuario (v2)
    objs = perfil.get("objetivos", {})
    deudas_txt = "\n".join(
        f"  - {d.get('nombre','?')}: saldo ~{d.get('saldo', 0):,} ARS, "
        f"cuota {d.get('cuota', 0):,}, vence día {d.get('vencimiento_dia','?')}. "
        f"{d.get('nota','')}"
        for d in perfil.get("deudas", [])
    ) or "  (sin deudas registradas)"

    cats_prio = ", ".join(objs.get("categorias_prioritarias", [])) or "ninguna"
    fecha_lim = objs.get("fecha_limite_deuda") or "sin fecha"
    obj_libre = objs.get("objetivo_libre", "") or "sin objetivo libre"

    return f"""CONTEXTO DEL USUARIO (no inventar nada que no esté acá):
- Ingreso mensual: {perfil.get('ingreso_mensual', 0):,} ARS
- Presupuesto del mes: {perfil.get('presupuesto_mes_actual', 0):,} ARS (techo)

OBJETIVOS DEL USUARIO:
- Ahorro mensual objetivo: {objs.get('ahorro_mensual', 0):,} ARS
- Máximo gasto innecesario: {objs.get('gasto_innecesario_max_pct', 20)}%
- Categorías prioritarias (lo importante para el user): {cats_prio}
- Fecha límite para liquidar deudas: {fecha_lim}
- Objetivo personal: {obj_libre}

Deudas:
{deudas_txt}

Plan: {perfil.get('plan', 'sin plan definido')}

PRIORIZÁ EL CONSEJO según los objetivos. Si pisa el techo o las prioridades, decilo claro.
"""


_PROMPT_REPORTE = """Sos el copiloto financiero del usuario. Hablás directo, en español rioplatense, sin jerga vacía.
Reglas:
- Cero clichés ("¡Qué genial!", "sigue así campeón"). Hablá como un amigo experto.
- Datos primero, recomendación después. Toda recomendación específica y accionable.
- Si va bien, decilo y explicá por qué. Si va mal, decilo sin dramatizar y proponé un ajuste concreto.
- Emojis con moderación (1-2 por sección).
- Largo: 8-15 líneas. Nada de párrafos infinitos.
- Si los datos son insuficientes, decilo y pedí más información.

IMPORTANTE - FORMATO:
- NO uses asteriscos (*) ni guiones bajos (_) para destacar texto. NADA de markdown.
- Usá texto plano y emojis para estructurar. Saltos de línea para separar secciones.
- Evitá símbolos como `, [, ], que rompen el parser de Telegram.
- Si necesitás listas, usá emojis (•, →, ✅, ⚠️) en vez de guiones.
"""


def analizar_reporte(tipo: str, datos: dict, usuario_id: int = None) -> str:
    try:
        return _llamar_modelo(
            modelo=MODEL_SMART,
            system=_PROMPT_REPORTE + "\n" + _contexto_financiero(usuario_id),
            user=(
                f"Generá un reporte tipo '{tipo}' con estos datos:\n\n"
                f"{json.dumps(datos, ensure_ascii=False, indent=2, default=str)}"
            ),
            max_tokens=900,
        )
    except Exception as e:
        log.exception("analizar_reporte falló")
        return f"⚠️ No pude generar el análisis ahora ({e})."


_PROMPT_DECISION = """Sos el copiloto financiero. El usuario te pregunta si conviene hacer un gasto.

FORMATO OBLIGATORIO (copiá estructura exacta):

⚖️ DECISIÓN
Veredicto: [elige UNO] 🟢 SÍ  o  🟡 CON CONDICIÓN  o  🔴 NO

Por qué:
→ [razón concreta 1]
→ [razón concreta 2]
→ [razón concreta 3 opcional]

Alternativa:
[qué hacer en cambio, máximo 2 líneas]

EJEMPLO DE RESPUESTA VÁLIDA:

⚖️ DECISIÓN
Veredicto: 🔴 NO

Por qué:
→ Ya gastaste 580k de 950k este mes (61% del presupuesto)
→ Tu objetivo es ahorrar 500k para atacar deuda en junio
→ Este gasto te saca 50k que necesitás para el plan

Alternativa:
Posponelo a julio cuando liquides Galicia, o buscá versión más económica.

REGLAS CRÍTICAS:
- SIEMPRE completar las 3 secciones: Veredicto + Por qué + Alternativa
- Por qué DEBE tener AL MENOS 2 razones concretas con números
- Texto plano, sin asteriscos ni markdown
- Considerá objetivos del usuario
"""


def evaluar_decision(consulta: str, datos: dict, usuario_id: int = None) -> str:
    try:
        return _llamar_modelo(
            modelo=MODEL_SMART,
            system=_PROMPT_DECISION + "\n" + _contexto_financiero(usuario_id),
            user=(
                f"Consulta: {consulta}\n\n"
                f"Estado actual:\n{json.dumps(datos, ensure_ascii=False, indent=2, default=str)}"
            ),
            max_tokens=500,
        )
    except Exception as e:
        log.exception("evaluar_decision falló")
        return f"⚠️ No pude evaluar la decisión ahora: {e}"
