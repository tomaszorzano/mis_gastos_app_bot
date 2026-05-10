"""
Tareas programadas vía JobQueue de python-telegram-bot.

Jobs:
- prompt_mensual: día 1 a las 10:00, pide resúmenes de tarjeta.
- recordatorio_mensual: días 3, 5, 8 a las 10:00, recuerda pendientes.
- prompt_nocturno: todos los días a las 22:00, pregunta si hubo gastos.
- catch_up_startup: al arrancar, avisa de pendientes acumulados.
"""
import logging
from datetime import time, date

import zoneinfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes

import storage
from config import (
    DIA_PROMPT_MENSUAL,
    HORA_PROMPT_MENSUAL,
    MINUTO_PROMPT_MENSUAL,
    HORA_PROMPT_NOCTURNO,
    MINUTO_PROMPT_NOCTURNO,
    TIMEZONE,
    TARJETAS_RESUMEN_MENSUAL,
    ALLOWED_USER_IDS,
)

log = logging.getLogger(__name__)
TZ = zoneinfo.ZoneInfo(TIMEZONE)


def _usuarios_a_notificar():
    """Iterador de user_ids que están autorizados Y registrados (chat_id)."""
    for uid in storage.listar_usuarios():
        if ALLOWED_USER_IDS and uid not in ALLOWED_USER_IDS:
            continue
        yield uid


# ----------------------------------------------------------------- #
# Prompt mensual                                                    #
# ----------------------------------------------------------------- #

async def job_prompt_mensual(ctx: ContextTypes.DEFAULT_TYPE):
    hoy = date.today()
    if hoy.day != DIA_PROMPT_MENSUAL:
        return
    mes = hoy.strftime("%Y-%m")
    for user_id in _usuarios_a_notificar():
        chat_id = storage.get_chat_id(user_id) or user_id
        # Usar tarjetas del perfil del usuario
        perfil = storage.obtener_perfil(user_id)
        tarjetas_user = perfil.get("tarjetas_trackear", [])
        if not tarjetas_user:
            continue
        pendientes = storage.tarjetas_pendientes_del_mes(user_id, tarjetas_user, mes)
        if not pendientes:
            continue
        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📅 *Empezó el mes* — {mes}\n\n"
                    f"¿Te llegaron los resúmenes de tarjeta?\n"
                    f"Pendientes: {', '.join(pendientes)}\n\n"
                    f"Mandá /tarjetas para registrarlos uno por uno."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            log.warning("No pude notificar a %s: %s", user_id, e)


async def job_recordatorio_mensual(ctx: ContextTypes.DEFAULT_TYPE):
    hoy = date.today()
    if hoy.day not in (3, 5, 8):
        return
    mes = hoy.strftime("%Y-%m")
    for user_id in _usuarios_a_notificar():
        chat_id = storage.get_chat_id(user_id) or user_id
        perfil = storage.obtener_perfil(user_id)
        tarjetas_user = perfil.get("tarjetas_trackear", [])
        if not tarjetas_user:
            continue
        pendientes = storage.tarjetas_pendientes_del_mes(user_id, tarjetas_user, mes)
        if not pendientes:
            continue
        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔔 Recordatorio: faltan resúmenes de "
                    f"{', '.join(pendientes)} para {mes}. /tarjetas cuando los tengas."
                ),
            )
        except Exception as e:
            log.warning("No pude notificar a %s: %s", user_id, e)


# ----------------------------------------------------------------- #
# Prompt nocturno                                                   #
# ----------------------------------------------------------------- #

async def job_prompt_nocturno(ctx: ContextTypes.DEFAULT_TYPE):
    """
    Chequea cada usuario y si su hora_recordatorio coincide con la hora actual,
    le pregunta si tuvo gastos.
    Solo manda si NO hay gastos cargados ni el día está marcado como sin gastos.
    """
    from datetime import datetime
    import zoneinfo
    tz = zoneinfo.ZoneInfo(TIMEZONE)
    hora_actual = datetime.now(tz).hour

    for user_id in _usuarios_a_notificar():
        # Obtener hora configurada del usuario
        perfil = storage.obtener_perfil(user_id)
        hora_user = perfil.get("hora_recordatorio", 22)
        
        # Solo notificar si es su hora
        if hora_user != hora_actual:
            continue

        if storage.tiene_actividad_hoy(user_id):
            continue  # ya cargó algo o marcó "sin gastos"
        
        chat_id = storage.get_chat_id(user_id) or user_id
        teclado = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Sí, cargar", callback_data="noche:si"),
                InlineKeyboardButton("⭕ No, día sin gastos", callback_data="noche:no"),
            ],
            [InlineKeyboardButton("⏰ Mañana te aviso", callback_data="noche:manana")],
        ])
        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text="🌙 ¿Tuviste gastos hoy?",
                reply_markup=teclado,
            )
        except Exception as e:
            log.warning("Prompt nocturno falló para %s: %s", user_id, e)


# ----------------------------------------------------------------- #
# Catch-up al startup                                               #
# ----------------------------------------------------------------- #

async def catch_up_startup(app: Application):
    hoy = date.today()
    mes = hoy.strftime("%Y-%m")
    for user_id in _usuarios_a_notificar():
        chat_id = storage.get_chat_id(user_id) or user_id
        # Resúmenes de tarjeta pendientes
        if hoy.day >= DIA_PROMPT_MENSUAL:
            perfil = storage.obtener_perfil(user_id)
            tarjetas_user = perfil.get("tarjetas_trackear", [])
            if not tarjetas_user:
                continue
            pendientes = storage.tarjetas_pendientes_del_mes(user_id, tarjetas_user, mes)
            if pendientes:
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"👋 Volví online. Faltan resúmenes de "
                            f"{', '.join(pendientes)} para {mes}.\n"
                            f"Mandá /tarjetas cuando los tengas."
                        ),
                    )
                except Exception as e:
                    log.warning("Catch-up tarjetas falló para %s: %s", user_id, e)


# ----------------------------------------------------------------- #
# Registro de jobs                                                  #
# ----------------------------------------------------------------- #

def registrar_jobs(app: Application) -> None:
    if app.job_queue is None:
        log.warning(
            "JobQueue no disponible. Instalá con: "
            "pip install 'python-telegram-bot[job-queue]'"
        )
        return

    horario_mensual = time(
        hour=HORA_PROMPT_MENSUAL,
        minute=MINUTO_PROMPT_MENSUAL,
        tzinfo=TZ,
    )

    app.job_queue.run_daily(job_prompt_mensual, time=horario_mensual,
                            name="prompt_mensual")
    app.job_queue.run_daily(job_recordatorio_mensual, time=horario_mensual,
                            name="recordatorio_mensual")
    
    # Nocturno corre CADA HORA al minuto 0 (00:00, 01:00, ..., 23:00)
    # Cada usuario tiene su hora_recordatorio en el perfil
    app.job_queue.run_repeating(
        job_prompt_nocturno,
        interval=3600,  # cada 1 hora (en segundos)
        first=60,  # empieza 60 seg después de arrancar
        name="prompt_nocturno_hourly",
    )

    log.info(
        "Jobs registrados: mensual %s · nocturno cada hora (personalizado por usuario) · TZ=%s",
        horario_mensual, TIMEZONE,
    )
