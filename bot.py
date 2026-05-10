"""
Bot de Telegram - Tracker de gastos con análisis inteligente.

Comandos:
  /start, /ayuda             → ayuda
  /nuevo                     → registrar gasto interactivamente
  /gasto <monto> <desc>      → registrar gasto rápido
  /tarjetas                  → cargar resúmenes mensuales
  /resumenes                 → ver resúmenes del mes
  /pagos                     → split por forma de pago (mes)
  /conciliar                 → comparar carga crédito vs resumen
  /hoy /semana /mes          → reportes
  /categoria /tendencia      → análisis adicional
  /proyeccion /alertas       → proyección y alertas
  /editar <id>               → editar un gasto
  /borrar <id>               → eliminar un gasto
  /presupuesto <monto>       → setear presupuesto
  /decision <consulta>       → "¿conviene gastar X?"
  /exportar                  → CSV
  /reset                     → borrar todos los datos
  /miid                      → ver mi user_id (para configurar autorización)
"""
import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
)

import storage
import ai_client
import reports
import alerts
import scheduler
import graphics
from parser import parsear_gasto
from config import (
    TELEGRAM_TOKEN, autorizado, presupuesto_diario,
    ALLOWED_USER_IDS,
)
from conversation import (
    conv_nuevo, conv_tarjetas, noche_no, noche_manana,
)
from profile_handlers import (
    conv_setup, conv_editar_objetivo, conv_agregar_deuda,
    cmd_perfil, cmd_reset_perfil, cmd_borrar_deuda,
    cmd_admin, cmd_aprobar, cmd_rechazar,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

EDIT_FIELD, EDIT_VALUE = range(20, 22)


# ----------------------------------------------------------------- #
# Auth helpers                                                      #
# ----------------------------------------------------------------- #

def _check_auth(update: Update) -> bool:
    return update.effective_user and autorizado(update.effective_user.id)


async def _denegar(update: Update):
    log.warning("Acceso denegado: user_id=%s",
                update.effective_user.id if update.effective_user else None)
    await update.message.reply_text(
        "⛔ Bot privado. Si querés acceso, pedile al admin que sume tu user_id "
        f"({update.effective_user.id if update.effective_user else 'desconocido'}) "
        f"a `ALLOWED_USER_IDS`.",
        parse_mode=ParseMode.MARKDOWN,
    )


def _registrar_chat(update: Update) -> None:
    if update.effective_user and update.effective_chat:
        storage.set_chat_id(update.effective_user.id, update.effective_chat.id)


# ----------------------------------------------------------------- #
# Comandos básicos                                                  #
# ----------------------------------------------------------------- #

AYUDA = """🤖 Tracker de Gastos

📥 Cargar gasto:
/nuevo — paso a paso
/gasto 500 super — rápido
2k uber — atajo

💳 Tarjetas:
/tarjetas — cargar resúmenes
/resumenes — ver del mes
/pagos — split por forma de pago
/conciliar — crédito vs resumen

📊 Reportes:
/hoy /semana /mes
/categoria /tendencia /proyeccion
/graficos — visuales interactivos

⚖️ Decisiones:
/decision ¿gasto X en Y?

⚙️ Tu perfil:
/setup — configurar todo (primera vez)
/perfil — ver tu config
/editar_objetivo — cambiar 1 cosa
/agregar_deuda /borrar_deuda

🔧 Otros:
/alertas /editar /borrar /presupuesto
/exportar /reset /cancelar /miid
"""


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else user.id
    _registrar_chat(update)

    if not autorizado(user.id):
        # Registrar solicitud de acceso para que admin lo apruebe
        nueva = storage.solicitar_acceso(
            user.id, user.username or "", user.first_name or "", chat_id
        )
        if nueva:
            # Avisar al admin
            from config import ADMIN_USER_ID
            try:
                admin_chat = storage.get_chat_id(ADMIN_USER_ID) or ADMIN_USER_ID
                uname = f"@{user.username}" if user.username else "(sin username)"
                await ctx.bot.send_message(
                    chat_id=admin_chat,
                    text=(
                        f"🔔 Nueva solicitud de acceso:\n"
                        f"  • {user.first_name} {uname}\n"
                        f"  • user_id: {user.id}\n\n"
                        f"Aprobá con: /aprobar {user.id}\n"
                        f"Rechazá con: /rechazar {user.id}"
                    ),
                )
            except Exception as e:
                log.warning("No pude avisar al admin: %s", e)

        await update.message.reply_text(
            f"👋 Hola {user.first_name}.\n\n"
            f"El bot es privado. Le avisé al admin que querés acceso.\n"
            f"Cuando te apruebe, te llega un mensaje acá.\n\n"
            f"Tu user_id: {user.id}"
        )
        return

    # Usuario autorizado
    perfil = storage.obtener_perfil(user.id)
    if not perfil.get("configurado"):
        await update.message.reply_text(
            f"👋 Hola {user.first_name}, soy tu copiloto financiero.\n\n"
            f"Para empezar, mandá /setup y configurá tu perfil en 2 min.\n"
            f"Después podés cargar gastos con /nuevo.\n\n"
            f"Mandá /ayuda para ver todos los comandos."
        )
    else:
        await update.message.reply_text(
            f"👋 Hola de vuelta, {user.first_name}.\n\n" + AYUDA
        )


async def cmd_miid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Devuelve el user_id del usuario. Útil para configurar autorización."""
    user = update.effective_user
    await update.message.reply_text(
        f"Tu user_id es: {user.id}\n"
        f"Autorizado: {'sí' if autorizado(user.id) else 'no'}"
    )


async def cmd_ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    await update.message.reply_text(AYUDA)


# ----------------------------------------------------------------- #
# Registro rápido de gastos                                         #
# ----------------------------------------------------------------- #

async def _procesar_gasto(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          mensaje: str):
    user_id = update.effective_user.id
    parsed = parsear_gasto(mensaje)
    if parsed is None:
        await update.message.reply_text(
            "❓ No detecté monto. Probá:\n"
            "• `/nuevo` (paso a paso)\n"
            "• `/gasto 500 supermercado`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    monto, moneda, descripcion = parsed
    pensando = await update.message.reply_text("🧠 Categorizando...")
    categorizacion = await asyncio.to_thread(
        ai_client.clasificar_gasto, monto, descripcion
    )
    # En modo rápido asumimos efectivo. Para forma_pago explícita usar /nuevo.
    gasto = storage.guardar_gasto(
        user_id, monto, descripcion, categorizacion,
        moneda=moneda, forma_pago="efectivo",
    )
    texto = reports.resumen_inmediato(user_id, gasto)
    activas = alerts.calcular_alertas(user_id, monto_recien=monto, moneda=moneda)
    if activas:
        texto += "\n\n" + alerts.formatear_alertas(activas)
    await pensando.edit_text(texto, parse_mode=ParseMode.MARKDOWN)


async def cmd_gasto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    await _procesar_gasto(update, ctx, update.message.text)


async def msg_libre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return  # no respondemos a no autorizados en mensajes libres
    _registrar_chat(update)
    texto = update.message.text or ""
    if parsear_gasto(texto) is None:
        return
    await _procesar_gasto(update, ctx, texto)


# ----------------------------------------------------------------- #
# Reportes                                                          #
# ----------------------------------------------------------------- #

async def _enviar_texto_largo(wait_msg, texto: str, intentar_markdown: bool = False):
    """
    Envía texto que puede ser largo o tener Markdown roto.
    
    Estrategia:
    1. Split en chunks de 3500 chars (margen para Telegram).
    2. El primer chunk reemplaza wait_msg con edit_text.
    3. Los siguientes se mandan como mensajes nuevos.
    4. NO usa Markdown por defecto (lo que viene del modelo es impredecible).
    """
    MAX = 3500
    if not texto:
        texto = "(respuesta vacía)"

    # Split por párrafos, manteniendo límite
    chunks = []
    actual = ""
    for parrafo in texto.split("\n\n"):
        if len(actual) + len(parrafo) + 2 <= MAX:
            actual = (actual + "\n\n" + parrafo) if actual else parrafo
        else:
            if actual:
                chunks.append(actual)
            # Si un solo párrafo es más largo que MAX, partirlo crudo
            while len(parrafo) > MAX:
                chunks.append(parrafo[:MAX])
                parrafo = parrafo[MAX:]
            actual = parrafo
    if actual:
        chunks.append(actual)

    # Primer chunk reemplaza el mensaje "Analizando..."
    parse_mode = ParseMode.MARKDOWN if intentar_markdown else None
    try:
        await wait_msg.edit_text(chunks[0], parse_mode=parse_mode)
    except Exception:
        # Si falla con Markdown, reintentar sin
        try:
            await wait_msg.edit_text(chunks[0])
        except Exception as e:
            log.warning("No pude editar mensaje: %s", e)

    # Siguientes chunks como mensajes nuevos
    chat_id = wait_msg.chat_id
    bot = wait_msg.get_bot()
    for chunk in chunks[1:]:
        try:
            await bot.send_message(chat_id=chat_id, text=chunk)
        except Exception as e:
            log.warning("No pude enviar chunk extra: %s", e)


def _wrapper_reporte(generator):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _check_auth(update):
            return await _denegar(update)
        _registrar_chat(update)
        wait = await update.message.reply_text("📊 Analizando...")
        try:
            texto = await asyncio.to_thread(generator, update.effective_user.id)
        except Exception as e:
            log.exception("Error generando reporte")
            texto = f"⚠️ Error: {e}"
        # Sin Markdown: lo que viene del modelo puede romper el parser
        await _enviar_texto_largo(wait, texto, intentar_markdown=False)
    return handler


cmd_hoy = _wrapper_reporte(reports.reporte_hoy)
cmd_semana = _wrapper_reporte(reports.reporte_semana)
cmd_mes = _wrapper_reporte(reports.reporte_mes)
cmd_categoria = _wrapper_reporte(reports.reporte_categorias)
cmd_tendencia = _wrapper_reporte(reports.reporte_tendencia)
cmd_proyeccion = _wrapper_reporte(reports.reporte_proyeccion)
cmd_pagos = _wrapper_reporte(reports.reporte_pagos)
cmd_conciliar = _wrapper_reporte(reports.conciliar_tarjeta)


async def cmd_alertas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    activas = alerts.calcular_alertas(update.effective_user.id)
    await update.message.reply_text(
        alerts.formatear_alertas(activas), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_resumenes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    texto = reports.reporte_tarjetas(update.effective_user.id)
    await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN)


# ----------------------------------------------------------------- #
# Decisión                                                          #
# ----------------------------------------------------------------- #

async def cmd_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    consulta = " ".join(ctx.args).strip()
    if not consulta:
        await update.message.reply_text(
            "Usá: `/decision ¿gasto 3000 en un viaje?`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    user_id = update.effective_user.id
    wait = await update.message.reply_text("🧠 Pensando...")

    from datetime import date
    from config import convertir_a_principal, MONEDA_PRINCIPAL
    desde = date.today().replace(day=1).isoformat()
    hasta = date.today().isoformat()
    gastos_mes = storage.obtener_gastos(user_id, desde, hasta)
    total_mes = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos_mes
    )
    objetivo = storage.get_presupuesto(user_id)["objetivo"]
    estado = {
        "fecha": hasta,
        "gastado_mes_principal": total_mes,
        "objetivo_mes": objetivo,
        "restante_mes": objetivo - total_mes,
        "presupuesto_diario": presupuesto_diario(),
        "dia_del_mes": date.today().day,
        "moneda_principal": MONEDA_PRINCIPAL,
        "resumenes_tarjeta_mes": storage.obtener_resumenes_tarjeta(
            user_id, mes=date.today().strftime("%Y-%m")
        ),
    }
    texto = await asyncio.to_thread(ai_client.evaluar_decision, consulta, estado, user_id)
    await _enviar_texto_largo(wait, texto, intentar_markdown=False)


# ----------------------------------------------------------------- #
# Gráficos                                                          #
# ----------------------------------------------------------------- #

async def cmd_graficos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    user_id = update.effective_user.id
    wait = await update.message.reply_text("📊 Generando gráficos...")
    
    try:
        html_file = await asyncio.to_thread(graphics.generar_html_graficos, user_id)
        with open(html_file, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=html_file.name,
                caption="📊 Tus gráficos del mes. Abrilo en el navegador.",
            )
        await wait.delete()
    except Exception as e:
        log.exception("Error generando gráficos")
        await wait.edit_text(f"⚠️ Error generando gráficos: {e}")


# ----------------------------------------------------------------- #
# Editar / borrar                                                   #
# ----------------------------------------------------------------- #

async def cmd_editar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    if not ctx.args:
        await update.message.reply_text("Usá: `/editar <id>`")
        return ConversationHandler.END
    gasto_id = ctx.args[0]
    g = storage.obtener_gasto_por_id(update.effective_user.id, gasto_id)
    if not g:
        await update.message.reply_text(f"No encontré gasto con id {gasto_id}")
        return ConversationHandler.END
    ctx.user_data["editando_id"] = gasto_id
    await update.message.reply_text(
        f"Editando: {g['monto']} {g.get('moneda', 'ARS')} · "
        f"{g.get('forma_pago','-')} · {g['categoria']} · {g['descripcion']}\n\n"
        f"¿Qué cambio? `monto`, `moneda`, `forma_pago`, `categoria` o `descripcion`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDIT_FIELD


async def edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = (update.message.text or "").strip().lower()
    if field not in {"monto", "moneda", "forma_pago", "categoria", "descripcion"}:
        await update.message.reply_text(
            "Campo inválido. Usá: monto, moneda, forma_pago, categoria, descripcion"
        )
        return EDIT_FIELD
    ctx.user_data["editando_campo"] = field
    await update.message.reply_text(
        f"Nuevo valor para `{field}`:", parse_mode=ParseMode.MARKDOWN
    )
    return EDIT_VALUE


async def edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    gasto_id = ctx.user_data.get("editando_id")
    field = ctx.user_data.get("editando_campo")
    valor = (update.message.text or "").strip()
    if field == "monto":
        try:
            valor = float(valor.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Monto inválido. Cancelado.")
            return ConversationHandler.END
    elif field == "moneda":
        valor = valor.upper()
        from config import MONEDAS
        if valor not in MONEDAS:
            await update.message.reply_text(f"Moneda inválida. Usá: {', '.join(MONEDAS)}")
            return ConversationHandler.END
    elif field == "forma_pago":
        valor = valor.lower()
        from config import FORMAS_PAGO
        if valor not in FORMAS_PAGO:
            await update.message.reply_text(f"Forma inválida. Usá: {', '.join(FORMAS_PAGO)}")
            return ConversationHandler.END
    actualizado = storage.actualizar_gasto(user_id, gasto_id, {field: valor})
    if actualizado:
        await update.message.reply_text(f"✅ Actualizado:\n{actualizado}")
    else:
        await update.message.reply_text("No pude actualizar.")
    ctx.user_data.clear()
    return ConversationHandler.END


async def edit_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Edición cancelada.")
    return ConversationHandler.END


async def cmd_borrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    if not ctx.args:
        await update.message.reply_text("Usá: `/borrar <id>`")
        return
    ok = storage.eliminar_gasto(update.effective_user.id, ctx.args[0])
    await update.message.reply_text("✅ Borrado" if ok else "No encontré ese ID")


# ----------------------------------------------------------------- #
# Presupuesto / exportar / reset                                    #
# ----------------------------------------------------------------- #

async def cmd_presupuesto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    if not ctx.args:
        p = storage.get_presupuesto(update.effective_user.id)
        await update.message.reply_text(
            f"Presupuesto actual: {p['objetivo']:,} ARS\n"
            f"Para cambiarlo: `/presupuesto 950000`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        monto = int(ctx.args[0].replace(".", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text("Monto inválido.")
        return
    storage.set_presupuesto(update.effective_user.id, monto)
    await update.message.reply_text(
        f"✅ Presupuesto seteado: {monto:,} ARS\nDiario: {monto // 30:,} ARS"
    )


async def cmd_exportar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    path = storage.exportar_csv(update.effective_user.id)
    with open(path, "rb") as f:
        await update.message.reply_document(
            document=f, filename=Path(path).name, caption="📁 Tus gastos"
        )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _check_auth(update):
        return await _denegar(update)
    _registrar_chat(update)
    if "CONFIRMAR" not in (ctx.args or []):
        await update.message.reply_text(
            "⚠️ Esto borra TODOS tus datos. Si estás seguro:\n`/reset CONFIRMAR`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    storage.reset_usuario(update.effective_user.id)
    await update.message.reply_text("🗑️ Datos reseteados.")


# ----------------------------------------------------------------- #
# Error handler                                                     #
# ----------------------------------------------------------------- #

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Excepción no capturada", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Ocurrió un error. Revisá logs."
            )
        except Exception:
            pass


# ----------------------------------------------------------------- #
# Setup                                                             #
# ----------------------------------------------------------------- #

async def post_init(app: Application):
    await scheduler.catch_up_startup(app)


def build_app() -> Application:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Falta TELEGRAM_TOKEN en variables de entorno")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Conversaciones (van primero para capturar /nuevo, /tarjetas y callbacks)
    app.add_handler(conv_nuevo())
    app.add_handler(conv_tarjetas())

    # v2: configuración de perfil
    app.add_handler(conv_setup())
    app.add_handler(conv_editar_objetivo())
    app.add_handler(conv_agregar_deuda())

    # Callbacks de noche que NO inician conversación
    app.add_handler(CallbackQueryHandler(noche_no, pattern="^noche:no$"))
    app.add_handler(CallbackQueryHandler(noche_manana, pattern="^noche:manana$"))

    # Editar
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("editar", cmd_editar)],
        states={
            EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
        },
        fallbacks=[CommandHandler("cancelar", edit_cancel)],
    )
    app.add_handler(edit_conv)

    # Comandos sueltos
    for name, handler in [
        ("start", cmd_start), ("ayuda", cmd_ayuda), ("help", cmd_ayuda),
        ("miid", cmd_miid), ("gasto", cmd_gasto),
        ("hoy", cmd_hoy), ("semana", cmd_semana), ("mes", cmd_mes),
        ("categoria", cmd_categoria), ("tendencia", cmd_tendencia),
        ("proyeccion", cmd_proyeccion), ("alertas", cmd_alertas),
        ("resumenes", cmd_resumenes), ("pagos", cmd_pagos),
        ("conciliar", cmd_conciliar), ("decision", cmd_decision),
        ("graficos", cmd_graficos),
        ("borrar", cmd_borrar), ("presupuesto", cmd_presupuesto),
        ("exportar", cmd_exportar), ("reset", cmd_reset),
        # v2 perfil
        ("perfil", cmd_perfil), ("reset_perfil", cmd_reset_perfil),
        ("borrar_deuda", cmd_borrar_deuda),
        # v2 admin
        ("admin", cmd_admin), ("aprobar", cmd_aprobar), ("rechazar", cmd_rechazar),
    ]:
        app.add_handler(CommandHandler(name, handler))

    # Mensajes libres - último
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_libre))

    app.add_error_handler(error_handler)
    scheduler.registrar_jobs(app)
    return app


def main():
    app = build_app()
    log.info("Bot iniciado. Usuarios autorizados: %s",
             ALLOWED_USER_IDS or "ABIERTO (no recomendado)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
