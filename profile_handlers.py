"""
Handlers v2 — Configuración del perfil financiero por usuario y panel de admin.

Flujos:
  /setup              → configura todo el perfil de una (ingreso, presupuesto,
                        objetivos, deudas, plan)
  /perfil             → muestra el perfil con botones para editar
  /editar_objetivo    → cambia un objetivo puntual
  /agregar_deuda      → agrega una deuda nueva
  /borrar_deuda       → elimina una deuda
  /admin              → solo admin: ver/aprobar/rechazar pendientes

Todo lo guardamos a través de storage.guardar_perfil() / actualizar_objetivo() /
agregar_deuda() / etc.
"""
import logging
from datetime import date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import storage
from config import (
    ADMIN_USER_ID,
    autorizado,
    CATEGORIAS,
)

log = logging.getLogger(__name__)

# Estados — namespace 200+ para no chocar con los de v1
SETUP_INGRESO, SETUP_PRESUPUESTO, SETUP_AHORRO, SETUP_INNECESARIO_PCT = range(200, 204)
SETUP_OBJ_LIBRE, SETUP_FECHA_DEUDA, SETUP_CATS_PRIO = range(204, 207)
SETUP_HORA_RECORDATORIO, SETUP_TARJETAS = range(207, 209)
SETUP_DEUDAS_DECIDIR, SETUP_DEUDA_NOMBRE, SETUP_DEUDA_SALDO = range(209, 212)
SETUP_DEUDA_CUOTA, SETUP_DEUDA_DIA, SETUP_DEUDA_NOTA = range(212, 215)
SETUP_PLAN, SETUP_CONFIRM = range(215, 217)

EDIT_OBJ_CAMPO, EDIT_OBJ_VALOR = range(220, 222)

ADD_DEUDA_NOMBRE, ADD_DEUDA_SALDO, ADD_DEUDA_CUOTA, ADD_DEUDA_DIA, ADD_DEUDA_NOTA = range(230, 235)

DEL_DEUDA_CONFIRMA = 240


# ----------------------------------------------------------------- #
# Helpers                                                           #
# ----------------------------------------------------------------- #

def _solo_autorizado(update: Update) -> bool:
    return update.effective_user and autorizado(update.effective_user.id)


def _parse_int(texto: str) -> int | None:
    """Parsea '950000', '950k', '38M', '950.000', '950,000' a int."""
    s = (texto or "").strip().lower().replace(".", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    multiplicador = 1
    if s.endswith("k"):
        multiplicador = 1_000
        s = s[:-1]
    elif s.endswith("m"):
        multiplicador = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * multiplicador)
    except ValueError:
        return None


def _resumen_perfil(perfil: dict) -> str:
    """Genera texto plano del perfil para mostrar."""
    if not perfil.get("configurado"):
        return (
            "📋 Tu perfil financiero\n\n"
            "Aún no configuraste tu perfil.\n"
            "Mandá /setup para hacerlo en 2 minutos."
        )

    objs = perfil.get("objetivos", {})
    lineas = [
        "📋 Tu perfil financiero",
        "",
        f"💰 Ingreso mensual: {perfil.get('ingreso_mensual', 0):,} ARS",
        f"🎯 Presupuesto del mes: {perfil.get('presupuesto_mes_actual', 0):,} ARS",
        f"💵 Ahorro objetivo: {objs.get('ahorro_mensual', 0):,} ARS/mes",
        f"⚠️ Tope gasto innecesario: {objs.get('gasto_innecesario_max_pct', 20)}%",
    ]

    cats = objs.get("categorias_prioritarias", [])
    if cats:
        lineas.append(f"📌 Categorías prioritarias: {', '.join(cats)}")

    if objs.get("fecha_limite_deuda"):
        lineas.append(f"📅 Fecha límite deuda: {objs['fecha_limite_deuda']}")

    if objs.get("objetivo_libre"):
        lineas.append(f"✨ Objetivo personal: {objs['objetivo_libre']}")

    tarjetas = perfil.get("tarjetas_trackear", [])
    if tarjetas:
        lineas.append(f"💳 Tarjetas a trackear: {', '.join(tarjetas)}")
    
    hora_rec = perfil.get("hora_recordatorio", 22)
    lineas.append(f"⏰ Recordatorio diario: {hora_rec}:00")

    deudas = perfil.get("deudas", [])
    if deudas:
        lineas.append("")
        lineas.append("💳 Deudas:")
        for d in deudas:
            lineas.append(
                f"  • {d.get('nombre','?')} | saldo {d.get('saldo',0):,} | "
                f"cuota {d.get('cuota',0):,} | día {d.get('vencimiento_dia','?')}"
                + (f" | {d.get('nota','')}" if d.get('nota') else "")
            )

    if perfil.get("plan"):
        lineas.append("")
        lineas.append(f"📝 Plan: {perfil['plan']}")

    lineas.append("")
    lineas.append("Para cambiar algo:")
    lineas.append("/editar_objetivo · /agregar_deuda · /borrar_deuda")
    return "\n".join(lineas)


# ----------------------------------------------------------------- #
# /setup — flujo completo                                           #
# ----------------------------------------------------------------- #

async def setup_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _solo_autorizado(update):
        await update.message.reply_text("⛔ Bot privado.")
        return ConversationHandler.END

    user_id = update.effective_user.id
    perfil = storage.obtener_perfil(user_id)
    if perfil.get("configurado"):
        await update.message.reply_text(
            "Ya tenés perfil configurado. Mandá /perfil para verlo,\n"
            "o /reset_perfil para empezar de cero.\n\n"
            "Para cambiar algo puntual: /editar_objetivo"
        )
        return ConversationHandler.END

    ctx.user_data["nuevo_perfil"] = {
        "ingreso_mensual": 0,
        "presupuesto_mes_actual": 0,
        "objetivos": {
            "ahorro_mensual": 0,
            "gasto_innecesario_max_pct": 20,
            "categorias_prioritarias": [],
            "fecha_limite_deuda": None,
            "objetivo_libre": "",
        },
        "deudas": [],
        "tarjetas_trackear": [],
        "hora_recordatorio": 22,
        "plan": "",
    }

    await update.message.reply_text(
        "👋 Vamos a configurar tu perfil financiero.\n"
        "Te pregunto cosas, vos respondés con números o texto.\n"
        "En cualquier momento podés /cancelar.\n\n"
        "💰 1/11 — ¿Cuál es tu ingreso mensual aproximado en ARS?\n"
        "(ejemplo: 3000000 o 3M)"
    )
    return SETUP_INGRESO


async def setup_ingreso(update, ctx):
    monto = _parse_int(update.message.text)
    if monto is None or monto <= 0:
        await update.message.reply_text("❓ Número inválido. Probá de nuevo:")
        return SETUP_INGRESO
    ctx.user_data["nuevo_perfil"]["ingreso_mensual"] = monto
    await update.message.reply_text(
        f"✅ Ingreso: {monto:,} ARS\n\n"
        "🎯 2/11 — ¿Cuánto querés gastar como máximo este mes?\n"
        "(presupuesto / techo de gastos)"
    )
    return SETUP_PRESUPUESTO


async def setup_presupuesto(update, ctx):
    monto = _parse_int(update.message.text)
    if monto is None or monto <= 0:
        await update.message.reply_text("❓ Número inválido:")
        return SETUP_PRESUPUESTO
    ctx.user_data["nuevo_perfil"]["presupuesto_mes_actual"] = monto
    await update.message.reply_text(
        f"✅ Presupuesto: {monto:,} ARS\n\n"
        "💵 3/11 — ¿Cuánto querés ahorrar por mes?\n"
        "(0 si todavía no es prioridad)"
    )
    return SETUP_AHORRO


async def setup_ahorro(update, ctx):
    monto = _parse_int(update.message.text)
    if monto is None or monto < 0:
        await update.message.reply_text("❓ Número inválido (puede ser 0):")
        return SETUP_AHORRO
    ctx.user_data["nuevo_perfil"]["objetivos"]["ahorro_mensual"] = monto
    await update.message.reply_text(
        f"✅ Ahorro objetivo: {monto:,} ARS\n\n"
        "⚠️ 4/11 — ¿Qué porcentaje máximo de tus gastos podés permitirte\n"
        "que sean 'innecesarios' (entretenimiento, antojos, etc)?\n"
        "Default 20%. Mandá un número o 'skip' para 20%."
    )
    return SETUP_INNECESARIO_PCT


async def setup_innecesario(update, ctx):
    txt = update.message.text.strip().lower()
    if txt == "skip":
        pct = 20
    else:
        pct = _parse_int(txt)
        if pct is None or pct < 0 or pct > 100:
            await update.message.reply_text("❓ Porcentaje inválido (0-100) o 'skip':")
            return SETUP_INNECESARIO_PCT
    ctx.user_data["nuevo_perfil"]["objetivos"]["gasto_innecesario_max_pct"] = pct

    cats_disponibles = ", ".join(CATEGORIAS)
    await update.message.reply_text(
        f"✅ Tope innecesario: {pct}%\n\n"
        f"📌 5/11 — ¿Qué categorías son PRIORIDAD para vos?\n"
        f"(separadas por coma, o 'skip' para ninguna)\n\n"
        f"Disponibles: {cats_disponibles}"
    )
    return SETUP_CATS_PRIO


async def setup_cats_prio(update, ctx):
    txt = update.message.text.strip()
    if txt.lower() == "skip":
        cats = []
    else:
        cats = [c.strip().capitalize() for c in txt.split(",") if c.strip()]
        # Validar que existan
        cats_validas = [c for c in cats if c in CATEGORIAS]
        cats = cats_validas
    ctx.user_data["nuevo_perfil"]["objetivos"]["categorias_prioritarias"] = cats
    await update.message.reply_text(
        f"✅ Prioritarias: {', '.join(cats) if cats else 'ninguna'}\n\n"
        f"📅 6/11 — ¿Tenés fecha límite para liquidar alguna deuda?\n"
        f"Formato: YYYY-MM-DD (ej: 2026-08-31), o 'skip'"
    )
    return SETUP_FECHA_DEUDA


async def setup_fecha_deuda(update, ctx):
    txt = update.message.text.strip().lower()
    if txt == "skip":
        fecha = None
    else:
        try:
            from datetime import datetime
            datetime.strptime(txt, "%Y-%m-%d")
            fecha = txt
        except ValueError:
            await update.message.reply_text("❓ Formato inválido (YYYY-MM-DD) o 'skip':")
            return SETUP_FECHA_DEUDA
    ctx.user_data["nuevo_perfil"]["objetivos"]["fecha_limite_deuda"] = fecha
    await update.message.reply_text(
        f"✅ Fecha límite: {fecha or 'ninguna'}\n\n"
        f"✨ 7/11 — ¿Algún objetivo personal en una frase?\n"
        f"(ej: 'comprarme una moto en diciembre', o 'skip')"
    )
    return SETUP_OBJ_LIBRE


async def setup_obj_libre(update, ctx):
    txt = update.message.text.strip()
    if txt.lower() == "skip":
        ctx.user_data["nuevo_perfil"]["objetivos"]["objetivo_libre"] = ""
    else:
        ctx.user_data["nuevo_perfil"]["objetivos"]["objetivo_libre"] = txt[:200]

    await update.message.reply_text(
        "⏰ 8/11 — ¿A qué hora querés que te recuerde si tuviste gastos?\n"
        "(número 0-23, ej: 22 para las 10pm, o 'skip' para default 22)"
    )
    return SETUP_HORA_RECORDATORIO


async def setup_hora_recordatorio(update, ctx):
    txt = update.message.text.strip().lower()
    if txt == "skip":
        hora = 22
    else:
        hora = _parse_int(txt)
        if hora is None or hora < 0 or hora > 23:
            await update.message.reply_text("❓ Hora inválida (0-23) o 'skip':")
            return SETUP_HORA_RECORDATORIO
    ctx.user_data["nuevo_perfil"]["hora_recordatorio"] = hora

    await update.message.reply_text(
        f"✅ Recordatorio: {hora}:00\n\n"
        "💳 9/11 — ¿Qué tarjetas de crédito querés trackear mes a mes?\n"
        "(nombres separados por coma, ej: 'Santander, Galicia')\n"
        "O 'skip' si no tenés tarjetas"
    )
    return SETUP_TARJETAS


async def setup_tarjetas(update, ctx):
    txt = update.message.text.strip()
    if txt.lower() == "skip":
        tarjetas = []
    else:
        tarjetas = [t.strip() for t in txt.split(",") if t.strip()]
    ctx.user_data["nuevo_perfil"]["tarjetas_trackear"] = tarjetas

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Agregar deuda", callback_data="setup_deuda:agregar"),
        InlineKeyboardButton("➡️ Sin deudas / Continuar", callback_data="setup_deuda:skip"),
    ]])
    await update.message.reply_text(
        f"✅ Tarjetas: {', '.join(tarjetas) if tarjetas else 'ninguna'}\n\n"
        "💳 10/11 — ¿Tenés alguna deuda que querés trackear?\n"
        "(préstamos, saldos de tarjeta, etc)",
        reply_markup=teclado,
    )
    return SETUP_DEUDAS_DECIDIR


async def setup_deudas_decidir(update, ctx):
    q = update.callback_query
    await q.answer()
    accion = q.data.split(":", 1)[1]

    if accion == "skip":
        return await _setup_pedir_plan(q, ctx)

    await q.edit_message_text("📛 Nombre de la deuda (ej: Santander, Galicia, Préstamo X):")
    return SETUP_DEUDA_NOMBRE


async def setup_deuda_nombre(update, ctx):
    nombre = update.message.text.strip()[:50]
    if not nombre:
        await update.message.reply_text("❓ Nombre vacío. Probá de nuevo:")
        return SETUP_DEUDA_NOMBRE
    ctx.user_data["deuda_actual"] = {"nombre": nombre}
    await update.message.reply_text(
        f"💳 {nombre}\n\n"
        "💰 ¿Saldo total aproximado en ARS?"
    )
    return SETUP_DEUDA_SALDO


async def setup_deuda_saldo(update, ctx):
    monto = _parse_int(update.message.text)
    if monto is None or monto < 0:
        await update.message.reply_text("❓ Número inválido:")
        return SETUP_DEUDA_SALDO
    ctx.user_data["deuda_actual"]["saldo"] = monto
    await update.message.reply_text(
        f"✅ Saldo: {monto:,} ARS\n\n"
        "💸 ¿Cuota mensual aproximada en ARS? (0 si no aplica)"
    )
    return SETUP_DEUDA_CUOTA


async def setup_deuda_cuota(update, ctx):
    monto = _parse_int(update.message.text)
    if monto is None or monto < 0:
        await update.message.reply_text("❓ Número inválido:")
        return SETUP_DEUDA_CUOTA
    ctx.user_data["deuda_actual"]["cuota"] = monto
    await update.message.reply_text(
        f"✅ Cuota: {monto:,} ARS\n\n"
        "📅 ¿Día de vencimiento del mes? (1-31)"
    )
    return SETUP_DEUDA_DIA


async def setup_deuda_dia(update, ctx):
    dia = _parse_int(update.message.text)
    if dia is None or dia < 1 or dia > 31:
        await update.message.reply_text("❓ Día inválido (1-31):")
        return SETUP_DEUDA_DIA
    ctx.user_data["deuda_actual"]["vencimiento_dia"] = dia
    await update.message.reply_text(
        f"✅ Día {dia}\n\n"
        "📝 ¿Alguna nota? (ej: 'refinanciación julio') o 'skip'"
    )
    return SETUP_DEUDA_NOTA


async def setup_deuda_nota(update, ctx):
    txt = update.message.text.strip()
    nota = "" if txt.lower() == "skip" else txt[:200]
    deuda = ctx.user_data["deuda_actual"]
    deuda["nota"] = nota
    deuda["id"] = str(__import__("uuid").uuid4())[:8]
    ctx.user_data["nuevo_perfil"]["deudas"].append(deuda)
    ctx.user_data.pop("deuda_actual", None)

    cant = len(ctx.user_data["nuevo_perfil"]["deudas"])
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Otra deuda", callback_data="setup_deuda:agregar"),
        InlineKeyboardButton("✅ Continuar", callback_data="setup_deuda:skip"),
    ]])
    await update.message.reply_text(
        f"✅ Deuda agregada. Total cargadas: {cant}\n\n"
        f"¿Querés agregar otra?",
        reply_markup=teclado,
    )
    return SETUP_DEUDAS_DECIDIR


async def _setup_pedir_plan(q_or_update, ctx):
    """Pide el plan estratégico. Se llama desde callback o desde setup_tarjetas."""
    msg_text = (
        "📝 11/11 — ¿Cuál es tu plan/estrategia para los próximos meses?\n"
        "Una frase o dos.\n"
        "Ej: 'Mayo control estricto, junio ataque a deuda'.\n"
        "O 'skip' si no tenés."
    )
    if hasattr(q_or_update, 'edit_message_text'):
        await q_or_update.edit_message_text(msg_text)
    else:
        await q_or_update.message.reply_text(msg_text)
    return SETUP_PLAN


async def setup_plan(update, ctx):
    txt = update.message.text.strip()
    plan = "" if txt.lower() == "skip" else txt[:500]
    ctx.user_data["nuevo_perfil"]["plan"] = plan

    user_id = update.effective_user.id
    storage.guardar_perfil(user_id, ctx.user_data["nuevo_perfil"])
    perfil = storage.obtener_perfil(user_id)

    await update.message.reply_text(
        "✅ Perfil configurado.\n\n" + _resumen_perfil(perfil)
    )
    ctx.user_data.pop("nuevo_perfil", None)
    return ConversationHandler.END


async def setup_cancelar(update, ctx):
    ctx.user_data.pop("nuevo_perfil", None)
    ctx.user_data.pop("deuda_actual", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Setup cancelado.")
    else:
        await update.message.reply_text("❌ Setup cancelado.")
    return ConversationHandler.END


def conv_setup() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("setup", setup_inicio)],
        states={
            SETUP_INGRESO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_ingreso)],
            SETUP_PRESUPUESTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_presupuesto)],
            SETUP_AHORRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_ahorro)],
            SETUP_INNECESARIO_PCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_innecesario)],
            SETUP_CATS_PRIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_cats_prio)],
            SETUP_FECHA_DEUDA: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_fecha_deuda)],
            SETUP_OBJ_LIBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_obj_libre)],
            SETUP_HORA_RECORDATORIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_hora_recordatorio)],
            SETUP_TARJETAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_tarjetas)],
            SETUP_DEUDAS_DECIDIR: [CallbackQueryHandler(setup_deudas_decidir, pattern="^setup_deuda:")],
            SETUP_DEUDA_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_deuda_nombre)],
            SETUP_DEUDA_SALDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_deuda_saldo)],
            SETUP_DEUDA_CUOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_deuda_cuota)],
            SETUP_DEUDA_DIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_deuda_dia)],
            SETUP_DEUDA_NOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_deuda_nota)],
            SETUP_PLAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_plan)],
        },
        fallbacks=[CommandHandler("cancelar", setup_cancelar)],
        conversation_timeout=900,
    )


# ----------------------------------------------------------------- #
# /perfil — solo muestra                                            #
# ----------------------------------------------------------------- #

async def cmd_perfil(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _solo_autorizado(update):
        await update.message.reply_text("⛔ Bot privado.")
        return
    user_id = update.effective_user.id
    perfil = storage.obtener_perfil(user_id)
    await update.message.reply_text(_resumen_perfil(perfil))


# ----------------------------------------------------------------- #
# /reset_perfil                                                     #
# ----------------------------------------------------------------- #

async def cmd_reset_perfil(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _solo_autorizado(update):
        return
    if "CONFIRMAR" not in (ctx.args or []):
        await update.message.reply_text(
            "⚠️ Esto borra tu perfil financiero (no los gastos).\n"
            "Si estás seguro: /reset_perfil CONFIRMAR"
        )
        return
    user_id = update.effective_user.id
    storage.guardar_perfil(user_id, {
        "configurado": False,
        "ingreso_mensual": 0,
        "presupuesto_mes_actual": 0,
        "objetivos": {
            "ahorro_mensual": 0,
            "gasto_innecesario_max_pct": 20,
            "categorias_prioritarias": [],
            "fecha_limite_deuda": None,
            "objetivo_libre": "",
        },
        "deudas": [],
        "plan": "",
    })
    # Volver a marcarlo como NO configurado
    todo = storage._cargar_todo()
    todo[str(user_id)]["perfil"]["configurado"] = False
    storage._guardar_todo(todo)
    await update.message.reply_text(
        "✅ Perfil reseteado. Mandá /setup para configurar de nuevo."
    )


# ----------------------------------------------------------------- #
# /editar_objetivo                                                  #
# ----------------------------------------------------------------- #

CAMPOS_EDITABLES = {
    "ingreso": ("ingreso_mensual", "número (ARS)"),
    "presupuesto": ("presupuesto_mes_actual", "número (ARS)"),
    "ahorro": ("ahorro_mensual", "número (ARS)"),
    "innecesario": ("gasto_innecesario_max_pct", "número 0-100 (%)"),
    "categorias": ("categorias_prioritarias", "lista separada por coma"),
    "tarjetas": ("tarjetas_trackear", "lista separada por coma"),
    "hora": ("hora_recordatorio", "número 0-23"),
    "fecha": ("fecha_limite_deuda", "YYYY-MM-DD o 'ninguna'"),
    "objetivo": ("objetivo_libre", "texto libre"),
    "plan": ("plan", "texto libre"),
}


async def editar_obj_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _solo_autorizado(update):
        return ConversationHandler.END
    perfil = storage.obtener_perfil(update.effective_user.id)
    if not perfil.get("configurado"):
        await update.message.reply_text("Necesitás configurar el perfil primero. /setup")
        return ConversationHandler.END

    opciones = "\n".join(
        f"  • {clave} — {desc}"
        for clave, (_, desc) in CAMPOS_EDITABLES.items()
    )
    await update.message.reply_text(
        "✏️ ¿Qué querés editar?\n\n"
        f"{opciones}\n\n"
        "Mandá la palabra (ej: 'ingreso')"
    )
    return EDIT_OBJ_CAMPO


async def editar_obj_campo(update, ctx):
    campo = update.message.text.strip().lower()
    if campo not in CAMPOS_EDITABLES:
        await update.message.reply_text(
            f"❓ Opción inválida. Usá una de: {', '.join(CAMPOS_EDITABLES.keys())}"
        )
        return EDIT_OBJ_CAMPO
    ctx.user_data["edit_obj_campo"] = campo
    _, desc = CAMPOS_EDITABLES[campo]
    await update.message.reply_text(f"📝 Nuevo valor para '{campo}' ({desc}):")
    return EDIT_OBJ_VALOR


async def editar_obj_valor(update, ctx):
    user_id = update.effective_user.id
    campo = ctx.user_data.get("edit_obj_campo")
    valor_raw = update.message.text.strip()
    campo_real, _ = CAMPOS_EDITABLES[campo]

    # Parseo según el tipo
    if campo in ("ingreso", "presupuesto", "ahorro"):
        valor = _parse_int(valor_raw)
        if valor is None or valor < 0:
            await update.message.reply_text("❌ Número inválido. Cancelado.")
            return ConversationHandler.END
    elif campo == "innecesario":
        valor = _parse_int(valor_raw)
        if valor is None or valor < 0 or valor > 100:
            await update.message.reply_text("❌ Porcentaje inválido (0-100). Cancelado.")
            return ConversationHandler.END
    elif campo == "hora":
        valor = _parse_int(valor_raw)
        if valor is None or valor < 0 or valor > 23:
            await update.message.reply_text("❌ Hora inválida (0-23). Cancelado.")
            return ConversationHandler.END
    elif campo == "categorias":
        cats = [c.strip().capitalize() for c in valor_raw.split(",") if c.strip()]
        valor = [c for c in cats if c in CATEGORIAS]
    elif campo == "tarjetas":
        valor = [t.strip() for t in valor_raw.split(",") if t.strip()]
    elif campo == "fecha":
        if valor_raw.lower() in ("ninguna", "skip", "no"):
            valor = None
        else:
            try:
                from datetime import datetime
                datetime.strptime(valor_raw, "%Y-%m-%d")
                valor = valor_raw
            except ValueError:
                await update.message.reply_text("❌ Formato inválido (YYYY-MM-DD). Cancelado.")
                return ConversationHandler.END
    else:
        valor = valor_raw[:500]

    storage.actualizar_objetivo(user_id, campo_real, valor)
    perfil = storage.obtener_perfil(user_id)
    await update.message.reply_text(
        f"✅ '{campo}' actualizado.\n\n" + _resumen_perfil(perfil)
    )
    ctx.user_data.pop("edit_obj_campo", None)
    return ConversationHandler.END


async def editar_obj_cancelar(update, ctx):
    ctx.user_data.pop("edit_obj_campo", None)
    await update.message.reply_text("❌ Edición cancelada.")
    return ConversationHandler.END


def conv_editar_objetivo() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("editar_objetivo", editar_obj_inicio)],
        states={
            EDIT_OBJ_CAMPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_obj_campo)],
            EDIT_OBJ_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, editar_obj_valor)],
        },
        fallbacks=[CommandHandler("cancelar", editar_obj_cancelar)],
        conversation_timeout=300,
    )


# ----------------------------------------------------------------- #
# /agregar_deuda                                                    #
# ----------------------------------------------------------------- #

async def add_deuda_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _solo_autorizado(update):
        return ConversationHandler.END
    ctx.user_data["nueva_deuda"] = {}
    await update.message.reply_text(
        "💳 Nueva deuda\n\n"
        "📛 Nombre (ej: Santander, Préstamo casa):"
    )
    return ADD_DEUDA_NOMBRE


async def add_deuda_nombre(update, ctx):
    nombre = update.message.text.strip()[:50]
    if not nombre:
        await update.message.reply_text("❓ Nombre vacío:")
        return ADD_DEUDA_NOMBRE
    ctx.user_data["nueva_deuda"]["nombre"] = nombre
    await update.message.reply_text("💰 Saldo total en ARS:")
    return ADD_DEUDA_SALDO


async def add_deuda_saldo(update, ctx):
    m = _parse_int(update.message.text)
    if m is None or m < 0:
        await update.message.reply_text("❓ Número inválido:")
        return ADD_DEUDA_SALDO
    ctx.user_data["nueva_deuda"]["saldo"] = m
    await update.message.reply_text("💸 Cuota mensual en ARS (0 si no aplica):")
    return ADD_DEUDA_CUOTA


async def add_deuda_cuota(update, ctx):
    m = _parse_int(update.message.text)
    if m is None or m < 0:
        await update.message.reply_text("❓ Número inválido:")
        return ADD_DEUDA_CUOTA
    ctx.user_data["nueva_deuda"]["cuota"] = m
    await update.message.reply_text("📅 Día de vencimiento (1-31):")
    return ADD_DEUDA_DIA


async def add_deuda_dia(update, ctx):
    d = _parse_int(update.message.text)
    if d is None or d < 1 or d > 31:
        await update.message.reply_text("❓ Día inválido (1-31):")
        return ADD_DEUDA_DIA
    ctx.user_data["nueva_deuda"]["vencimiento_dia"] = d
    await update.message.reply_text("📝 Nota (o 'skip'):")
    return ADD_DEUDA_NOTA


async def add_deuda_nota(update, ctx):
    txt = update.message.text.strip()
    nota = "" if txt.lower() == "skip" else txt[:200]
    deuda = ctx.user_data["nueva_deuda"]
    deuda["nota"] = nota

    user_id = update.effective_user.id
    storage.agregar_deuda(user_id, deuda)
    await update.message.reply_text(
        f"✅ Deuda '{deuda['nombre']}' agregada.\n"
        f"Mandá /perfil para ver todas."
    )
    ctx.user_data.pop("nueva_deuda", None)
    return ConversationHandler.END


async def add_deuda_cancelar(update, ctx):
    ctx.user_data.pop("nueva_deuda", None)
    await update.message.reply_text("❌ Cancelado.")
    return ConversationHandler.END


def conv_agregar_deuda() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("agregar_deuda", add_deuda_inicio)],
        states={
            ADD_DEUDA_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deuda_nombre)],
            ADD_DEUDA_SALDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deuda_saldo)],
            ADD_DEUDA_CUOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deuda_cuota)],
            ADD_DEUDA_DIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deuda_dia)],
            ADD_DEUDA_NOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_deuda_nota)],
        },
        fallbacks=[CommandHandler("cancelar", add_deuda_cancelar)],
        conversation_timeout=300,
    )


# ----------------------------------------------------------------- #
# /borrar_deuda                                                     #
# ----------------------------------------------------------------- #

async def cmd_borrar_deuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _solo_autorizado(update):
        return
    user_id = update.effective_user.id
    perfil = storage.obtener_perfil(user_id)
    deudas = perfil.get("deudas", [])
    if not deudas:
        await update.message.reply_text("No tenés deudas registradas.")
        return

    if not ctx.args:
        lineas = ["💳 Tus deudas:"]
        for d in deudas:
            lineas.append(f"  • {d.get('id','?')} — {d.get('nombre','?')}")
        lineas.append("\nPara borrar: /borrar_deuda <id>")
        await update.message.reply_text("\n".join(lineas))
        return

    deuda_id = ctx.args[0]
    ok = storage.eliminar_deuda(user_id, deuda_id)
    if ok:
        await update.message.reply_text(f"✅ Deuda {deuda_id} eliminada.")
    else:
        await update.message.reply_text(f"❌ No encontré deuda con id {deuda_id}")


# ----------------------------------------------------------------- #
# /admin                                                            #
# ----------------------------------------------------------------- #

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Solo el admin puede usar este comando.")
        return

    pendientes = storage.listar_pendientes()
    stats = storage.stats_globales()

    lineas = [
        "🛡️ Panel de Admin",
        "",
        f"📊 Stats:",
        f"  • Usuarios totales: {stats['total_usuarios']}",
        f"  • Configurados: {stats['usuarios_configurados']}",
        f"  • Pendientes de aprobación: {stats['usuarios_pendientes']}",
    ]

    if pendientes:
        lineas.append("")
        lineas.append("⏳ Solicitudes pendientes:")
        for p in pendientes:
            uname = f"@{p['username']}" if p.get('username') else "(sin username)"
            nombre = p.get('first_name', '')
            lineas.append(f"  • {p['user_id']} — {nombre} {uname}")
        lineas.append("")
        lineas.append("Para aprobar: /aprobar <user_id>")
        lineas.append("Para rechazar: /rechazar <user_id>")
    else:
        lineas.append("")
        lineas.append("✅ Sin solicitudes pendientes.")

    lineas.append("")
    lineas.append(
        "Nota: aprobar genera el aviso al usuario, pero también tenés que\n"
        "agregarlo a la env var ALLOWED_USER_IDS en Replit y reiniciar."
    )

    await update.message.reply_text("\n".join(lineas))


async def cmd_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usá: /aprobar <user_id>")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("user_id inválido.")
        return

    pendiente = storage.aprobar_usuario(uid)
    if not pendiente:
        await update.message.reply_text(f"No encontré pendiente con id {uid}")
        return

    # Avisar al usuario aprobado
    try:
        await ctx.bot.send_message(
            chat_id=pendiente["chat_id"],
            text=(
                "🎉 Tu solicitud fue aprobada.\n"
                "Mandá /setup para configurar tu perfil financiero."
            ),
        )
    except Exception as e:
        log.warning("No pude avisar al user aprobado: %s", e)

    await update.message.reply_text(
        f"✅ Aprobado {uid}.\n\n"
        f"⚠️ ACORDATE: agregá '{uid}' a ALLOWED_USER_IDS en Replit Secrets\n"
        f"y reiniciá el bot. Sin esto NO va a poder operar."
    )


async def cmd_rechazar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usá: /rechazar <user_id>")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        return

    pendiente = storage.rechazar_usuario(uid)
    if not pendiente:
        await update.message.reply_text(f"No encontré pendiente con id {uid}")
        return

    try:
        await ctx.bot.send_message(
            chat_id=pendiente["chat_id"],
            text="❌ Tu solicitud fue rechazada.",
        )
    except Exception:
        pass
    await update.message.reply_text(f"✅ Rechazado {uid}.")
