"""
Flujos interactivos basados en ConversationHandler.

Conversaciones:
  /nuevo       → registra un gasto: monto → moneda → forma_pago → desc → confirm
  /tarjetas    → carga resúmenes mensuales de cada tarjeta configurada.
  /noche_sí    → callback del recordatorio nocturno: abre /nuevo
  /noche_no    → callback: marca el día como "sin gastos"
"""
import asyncio
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
import ai_client
import reports
import alerts
from config import (
    MONEDAS, MONEDA_PRINCIPAL, TARJETAS_RESUMEN_MENSUAL,
    FORMAS_PAGO, FORMA_PAGO_LABELS,
)

log = logging.getLogger(__name__)

# Estados (namespaces aparte para no chocar)
NUEVO_MONTO, NUEVO_MONEDA, NUEVO_FORMA, NUEVO_DESC, NUEVO_CONFIRMA = range(5)
TARJ_MONTO, TARJ_MONEDA = range(10, 12)


# ----------------------------------------------------------------- #
# Helpers UI                                                        #
# ----------------------------------------------------------------- #

def _teclado_monedas(prefix: str) -> InlineKeyboardMarkup:
    botones = [InlineKeyboardButton(m, callback_data=f"{prefix}:{m}") for m in MONEDAS]
    filas = [botones[i:i+2] for i in range(0, len(botones), 2)]
    return InlineKeyboardMarkup(filas)


def _teclado_formas_pago(prefix: str) -> InlineKeyboardMarkup:
    botones = [
        InlineKeyboardButton(FORMA_PAGO_LABELS[fp], callback_data=f"{prefix}:{fp}")
        for fp in FORMAS_PAGO
    ]
    filas = [botones[i:i+2] for i in range(0, len(botones), 2)]
    return InlineKeyboardMarkup(filas)


def _teclado_confirma() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar", callback_data="conf:ok"),
        InlineKeyboardButton("❌ Descartar", callback_data="conf:cancel"),
    ]])


# ----------------------------------------------------------------- #
# /nuevo                                                            #
# ----------------------------------------------------------------- #

async def nuevo_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nuevo_gasto"] = {}
    msg = update.message or (update.callback_query and update.callback_query.message)
    await msg.reply_text(
        "💰 ¿*Cuánto* gastaste? Mandame solo el número.\n"
        "(/cancelar para salir)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return NUEVO_MONTO


async def nuevo_monto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    texto = (update.message.text or "").strip()
    from parser import _normalizar_monto, _MONTO_RE, _detectar_moneda
    moneda_detectada, texto_sin_moneda = _detectar_moneda(texto)
    m = _MONTO_RE.search(texto_sin_moneda)
    if not m:
        await update.message.reply_text("❓ No detecté un número. Probá de nuevo:")
        return NUEVO_MONTO
    monto = _normalizar_monto(m.group("monto"), bool(m.group("k")))
    if not monto or monto <= 0:
        await update.message.reply_text("❓ Monto inválido. Probá de nuevo:")
        return NUEVO_MONTO

    ctx.user_data["nuevo_gasto"]["monto"] = monto
    if moneda_detectada != MONEDA_PRINCIPAL or any(
        tok in texto.lower() for tok in ("ars", "peso")
    ):
        ctx.user_data["nuevo_gasto"]["moneda"] = moneda_detectada
        await update.message.reply_text(
            f"💱 Moneda: *{moneda_detectada}*\n\n"
            f"💳 ¿Con qué *pagaste*?",
            reply_markup=_teclado_formas_pago("nfp"),
            parse_mode=ParseMode.MARKDOWN,
        )
        return NUEVO_FORMA

    await update.message.reply_text(
        f"💱 ¿En qué *moneda*? ({int(monto):,})",
        reply_markup=_teclado_monedas("ngm"),
        parse_mode=ParseMode.MARKDOWN,
    )
    return NUEVO_MONEDA


async def nuevo_moneda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    moneda = q.data.split(":", 1)[1]
    ctx.user_data["nuevo_gasto"]["moneda"] = moneda
    monto = ctx.user_data["nuevo_gasto"]["monto"]
    await q.edit_message_text(
        f"💰 {int(monto):,} *{moneda}*\n\n"
        f"💳 ¿Con qué *pagaste*?",
        reply_markup=_teclado_formas_pago("nfp"),
        parse_mode=ParseMode.MARKDOWN,
    )
    return NUEVO_FORMA


async def nuevo_forma(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    forma = q.data.split(":", 1)[1]
    if forma not in FORMAS_PAGO:
        await q.edit_message_text("❌ Forma inválida.")
        return ConversationHandler.END
    ctx.user_data["nuevo_gasto"]["forma_pago"] = forma
    g = ctx.user_data["nuevo_gasto"]
    await q.edit_message_text(
        f"💰 {int(g['monto']):,} {g['moneda']} · {FORMA_PAGO_LABELS[forma]}\n\n"
        f"📝 ¿En qué fue? (descripción corta)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return NUEVO_DESC


async def nuevo_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = (update.message.text or "").strip()
    if not desc:
        await update.message.reply_text("📝 Mandame una descripción no vacía:")
        return NUEVO_DESC

    g = ctx.user_data["nuevo_gasto"]
    g["descripcion"] = desc

    pensando = await update.message.reply_text("🧠 Categorizando...")
    cat = await asyncio.to_thread(
        ai_client.clasificar_gasto, g["monto"], desc
    )
    g["categorizacion"] = cat

    resumen = (
        f"📋 *Confirmá el gasto:*\n\n"
        f"💰 {int(g['monto']):,} {g['moneda']}\n"
        f"{FORMA_PAGO_LABELS[g['forma_pago']]}\n"
        f"📝 {desc}\n"
        f"📂 {cat['categoria']} ({cat['necesidad']}) · {cat['tipo']}"
    )
    await pensando.edit_text(
        resumen, reply_markup=_teclado_confirma(), parse_mode=ParseMode.MARKDOWN
    )
    return NUEVO_CONFIRMA


async def nuevo_confirma(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    accion = q.data.split(":", 1)[1]
    g = ctx.user_data.get("nuevo_gasto", {})

    if accion != "ok":
        await q.edit_message_text("❌ Descartado.")
        ctx.user_data.pop("nuevo_gasto", None)
        return ConversationHandler.END

    user_id = update.effective_user.id
    gasto = storage.guardar_gasto(
        user_id, g["monto"], g["descripcion"],
        g["categorizacion"], moneda=g["moneda"],
        forma_pago=g["forma_pago"],
    )
    texto = reports.resumen_inmediato(user_id, gasto)
    activas = alerts.calcular_alertas(user_id, monto_recien=g["monto"], moneda=g["moneda"])
    if activas:
        texto += "\n\n" + alerts.formatear_alertas(activas)

    await q.edit_message_text(texto, parse_mode=ParseMode.MARKDOWN)
    ctx.user_data.pop("nuevo_gasto", None)
    return ConversationHandler.END


async def cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("nuevo_gasto", None)
    ctx.user_data.pop("tarjeta_actual", None)
    ctx.user_data.pop("tarjetas_pendientes", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Cancelado.")
    else:
        await update.message.reply_text("❌ Cancelado.")
    return ConversationHandler.END


def conv_nuevo() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("nuevo", nuevo_inicio),
            CallbackQueryHandler(nuevo_inicio, pattern="^noche:si$"),
        ],
        states={
            NUEVO_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_monto)],
            NUEVO_MONEDA: [CallbackQueryHandler(nuevo_moneda, pattern="^ngm:")],
            NUEVO_FORMA: [CallbackQueryHandler(nuevo_forma, pattern="^nfp:")],
            NUEVO_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, nuevo_desc)],
            NUEVO_CONFIRMA: [CallbackQueryHandler(nuevo_confirma, pattern="^conf:")],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
        conversation_timeout=300,
    )


# ----------------------------------------------------------------- #
# Recordatorio nocturno: callbacks "Sí cargué"/"No, día sin gastos" #
# ----------------------------------------------------------------- #

async def noche_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usuario marca el día como sin gastos."""
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id
    storage.marcar_dia_sin_gastos(user_id)
    await q.edit_message_text("✅ Día marcado como sin gastos. Buenas noches.")


async def noche_manana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("👍 Te pregunto mañana de nuevo.")


# ----------------------------------------------------------------- #
# /tarjetas                                                         #
# ----------------------------------------------------------------- #

async def tarjetas_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          tarjetas_a_preguntar: list[str] | None = None,
                          mes: str | None = None,
                          chat_id: int | None = None):
    user_id = update.effective_user.id if update else ctx.job.user_id  # type: ignore
    chat_id = chat_id or (update.effective_chat.id if update else ctx.job.chat_id)  # type: ignore
    mes = mes or date.today().strftime("%Y-%m")

    if tarjetas_a_preguntar is None:
        # Usar tarjetas del perfil del usuario
        perfil = storage.obtener_perfil(user_id)
        tarjetas_user = perfil.get("tarjetas_trackear", [])
        if not tarjetas_user:
            msg_text = (
                "No tenés tarjetas configuradas en tu perfil.\n"
                "Mandá /editar_objetivo y elegí 'tarjetas' para agregar."
            )
            if update:
                await update.message.reply_text(msg_text)
            else:
                await ctx.bot.send_message(chat_id=chat_id, text=msg_text)
            return ConversationHandler.END
        
        tarjetas_a_preguntar = storage.tarjetas_pendientes_del_mes(
            user_id, tarjetas_user, mes
        )

    if not tarjetas_a_preguntar:
        msg_text = "✅ Ya cargaste todos los resúmenes del mes."
        if update:
            await update.message.reply_text(msg_text)
        else:
            await ctx.bot.send_message(chat_id=chat_id, text=msg_text)
        return ConversationHandler.END

    ctx.user_data["tarjetas_pendientes"] = list(tarjetas_a_preguntar)
    ctx.user_data["tarj_mes"] = mes

    encabezado = (
        f"📅 *Resúmenes de tarjeta — {mes}*\n\n"
        f"Te pregunto una por una. `skip` si todavía no llegó.\n"
        f"Pendientes: {', '.join(tarjetas_a_preguntar)}"
    )
    if update:
        await update.message.reply_text(encabezado, parse_mode=ParseMode.MARKDOWN)
    else:
        await ctx.bot.send_message(chat_id=chat_id, text=encabezado, parse_mode=ParseMode.MARKDOWN)

    return await _preguntar_siguiente_tarjeta(update, ctx, chat_id)


async def _preguntar_siguiente_tarjeta(update, ctx, chat_id):
    pendientes = ctx.user_data.get("tarjetas_pendientes", [])
    if not pendientes:
        cierre = "✅ Listo, cargaste todos los resúmenes."
        if update and update.message:
            await update.message.reply_text(cierre)
        else:
            await ctx.bot.send_message(chat_id=chat_id, text=cierre)
        ctx.user_data.pop("tarjetas_pendientes", None)
        ctx.user_data.pop("tarj_mes", None)
        return ConversationHandler.END

    tarjeta = pendientes[0]
    ctx.user_data["tarjeta_actual"] = tarjeta
    pregunta = (
        f"💳 *{tarjeta}* — ¿cuánto te llegó este mes?\n"
        f"(número, o `skip` si no llegó)"
    )
    if update and update.message:
        await update.message.reply_text(pregunta, parse_mode=ParseMode.MARKDOWN)
    else:
        await ctx.bot.send_message(chat_id=chat_id, text=pregunta, parse_mode=ParseMode.MARKDOWN)
    return TARJ_MONTO


async def tarj_monto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    texto = (update.message.text or "").strip().lower()
    chat_id = update.effective_chat.id

    if texto == "skip":
        ctx.user_data.get("tarjetas_pendientes", []).pop(0)
        ctx.user_data.pop("tarjeta_actual", None)
        return await _preguntar_siguiente_tarjeta(update, ctx, chat_id)

    from parser import _normalizar_monto, _MONTO_RE, _detectar_moneda
    moneda_det, texto_clean = _detectar_moneda(texto)
    m = _MONTO_RE.search(texto_clean)
    if not m:
        await update.message.reply_text("❓ Número inválido. Probá de nuevo o `skip`:")
        return TARJ_MONTO
    monto = _normalizar_monto(m.group("monto"), bool(m.group("k")))
    if not monto or monto <= 0:
        await update.message.reply_text("❓ Monto inválido. Probá de nuevo:")
        return TARJ_MONTO

    ctx.user_data["tarj_monto"] = monto

    if moneda_det != MONEDA_PRINCIPAL or any(t in texto for t in ("ars", "peso")):
        return await _tarj_finalizar(update, ctx, moneda_det)

    await update.message.reply_text(
        f"💱 ¿En qué moneda? ({int(monto):,})",
        reply_markup=_teclado_monedas("tcm"),
    )
    return TARJ_MONEDA


async def tarj_moneda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    moneda = q.data.split(":", 1)[1]
    return await _tarj_finalizar(update, ctx, moneda, via_callback=True)


async def _tarj_finalizar(update, ctx, moneda, via_callback=False):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    tarjeta = ctx.user_data.get("tarjeta_actual")
    monto = ctx.user_data.get("tarj_monto")
    mes = ctx.user_data.get("tarj_mes", date.today().strftime("%Y-%m"))

    if not (tarjeta and monto):
        msg_text = "❌ Algo se perdió. /tarjetas para reintentar."
        if via_callback:
            await update.callback_query.edit_message_text(msg_text)
        else:
            await update.message.reply_text(msg_text)
        return ConversationHandler.END

    storage.guardar_resumen_tarjeta(user_id, tarjeta, monto, moneda=moneda, mes=mes)
    confirm = f"✅ {tarjeta}: {int(monto):,} {moneda} (mes {mes})"
    if via_callback:
        await update.callback_query.edit_message_text(confirm)
    else:
        await update.message.reply_text(confirm)

    ctx.user_data.get("tarjetas_pendientes", []).pop(0)
    ctx.user_data.pop("tarjeta_actual", None)
    ctx.user_data.pop("tarj_monto", None)
    return await _preguntar_siguiente_tarjeta(update, ctx, chat_id)


def conv_tarjetas() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("tarjetas", tarjetas_inicio)],
        states={
            TARJ_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, tarj_monto)],
            TARJ_MONEDA: [CallbackQueryHandler(tarj_moneda, pattern="^tcm:")],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
        conversation_timeout=600,
    )
