"""
Reglas de alertas. Determinísticas (no llaman a Claude).
Multi-moneda: convertimos a moneda principal antes de comparar contra
límites/presupuesto.
"""
from datetime import date

import storage
from config import (
    PERFIL_FINANCIERO,
    GASTO_GRANDE_PCT,
    DESVIO_PROYECCION_PCT,
    presupuesto_diario,
    convertir_a_principal,
    MONEDA_PRINCIPAL,
)


def _rango_mes_actual():
    hoy = date.today()
    return hoy.replace(day=1).isoformat(), hoy.isoformat()


def alerta_gasto_grande(monto: float, moneda: str = MONEDA_PRINCIPAL) -> dict | None:
    monto_principal = convertir_a_principal(monto, moneda)
    limite_d = presupuesto_diario()
    if monto_principal >= limite_d * GASTO_GRANDE_PCT:
        nota_moneda = "" if moneda == MONEDA_PRINCIPAL else f" (≈ {int(monto_principal):,} {MONEDA_PRINCIPAL})"
        return {
            "tipo": "gasto_grande",
            "icono": "⚠️",
            "titulo": "Gasto grande",
            "mensaje": (
                f"Registraste {int(monto):,} {moneda}{nota_moneda}, que es "
                f"{monto_principal/limite_d*100:.0f}% del límite diario ({limite_d:,} {MONEDA_PRINCIPAL}). "
                f"Si fue error, usá /editar."
            ),
        }
    return None


def _total_dia_principal(usuario_id: int, fecha_iso: str) -> float:
    return sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in storage.obtener_gastos(usuario_id, fecha_iso, fecha_iso)
    )


def alerta_excedio_diario(usuario_id: int) -> dict | None:
    hoy = date.today().isoformat()
    total_hoy = _total_dia_principal(usuario_id, hoy)
    limite = presupuesto_diario()
    if total_hoy > limite:
        return {
            "tipo": "excedio_diario",
            "icono": "🚨",
            "titulo": "Superaste el presupuesto diario",
            "mensaje": (
                f"Hoy llevás {int(total_hoy):,} {MONEDA_PRINCIPAL} (límite {limite:,}). "
                f"Mayo es de control: cada peso de más hoy sale del ataque a deuda en junio."
            ),
        }
    return None


def alerta_proyeccion(usuario_id: int) -> dict | None:
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde, hasta)
    total = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos
    )
    dias = date.today().day
    if dias < 5 or not total:
        return None
    proyeccion = (total / dias) * 30
    objetivo = storage.get_presupuesto(usuario_id)["objetivo"]
    desvio = (proyeccion - objetivo) / objetivo
    if desvio > DESVIO_PROYECCION_PCT:
        return {
            "tipo": "proyeccion_alta",
            "icono": "📈",
            "titulo": "Proyección sobre presupuesto",
            "mensaje": (
                f"Al ritmo actual cerrás el mes en {int(proyeccion):,} {MONEDA_PRINCIPAL} "
                f"({desvio*100:+.0f}% vs objetivo de {objetivo:,}). "
                f"Necesitás bajar a ~{int((objetivo - total)/(30 - dias)):,}/día "
                f"por los {30-dias} días restantes."
            ),
        }
    if desvio < -DESVIO_PROYECCION_PCT:
        return {
            "tipo": "proyeccion_baja",
            "icono": "✅",
            "titulo": "Proyección bajo presupuesto",
            "mensaje": (
                f"Vas a cerrar en ~{int(proyeccion):,} {MONEDA_PRINCIPAL}, "
                f"{int(objetivo-proyeccion):,} bajo el objetivo. "
                f"Ese excedente puede ir directo a tarjeta en junio."
            ),
        }
    return None


def alerta_innecesario_alto(usuario_id: int) -> dict | None:
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde, hasta)
    total = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos
    )
    if total == 0:
        return None
    innecesario = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos if g["necesidad"] == "innecesario"
    )
    pct = innecesario / total * 100
    objetivo_pct = PERFIL_FINANCIERO["objetivo_innecesario_pct"]
    if pct > objetivo_pct:
        return {
            "tipo": "innecesario_alto",
            "icono": "📊",
            "titulo": "Gasto innecesario sobre objetivo",
            "mensaje": (
                f"Llevás {pct:.0f}% en innecesario (objetivo: <{objetivo_pct}%). "
                f"Eso son {int(innecesario):,} {MONEDA_PRINCIPAL} este mes."
            ),
        }
    return None


def alerta_vencimiento_cercano() -> list[dict]:
    hoy = date.today()
    out = []
    for d in PERFIL_FINANCIERO["deudas"]:
        try:
            venc = hoy.replace(day=d["vencimiento_dia"])
        except ValueError:
            continue
        if venc < hoy:
            mes = hoy.month + 1 if hoy.month < 12 else 1
            anio = hoy.year if hoy.month < 12 else hoy.year + 1
            try:
                venc = date(anio, mes, d["vencimiento_dia"])
            except ValueError:
                continue
        delta = (venc - hoy).days
        if 0 <= delta <= 5:
            cuota = d.get("cuota", d.get("cuota_aprox", 0))
            out.append({
                "tipo": "vencimiento",
                "icono": "📅",
                "titulo": f"Vencimiento {d['nombre']} en {delta} días",
                "mensaje": f"{d['nombre']}: {venc.isoformat()} — cuota ~{cuota:,} {MONEDA_PRINCIPAL}. {d['nota']}",
            })
    return out


def alerta_resumen_tarjeta_pendiente(usuario_id: int) -> dict | None:
    """Si pasaron días del 1 y aún no cargó resúmenes, recordamos."""
    hoy = date.today()
    if hoy.day < 2 or hoy.day > 10:
        return None
    mes = hoy.strftime("%Y-%m")
    pendientes = storage.tarjetas_pendientes_del_mes(
        usuario_id,
        [d["nombre"] for d in PERFIL_FINANCIERO["deudas"]],
        mes,
    )
    if not pendientes:
        return None
    return {
        "tipo": "tarjeta_pendiente",
        "icono": "💳",
        "titulo": "Resúmenes pendientes",
        "mensaje": f"Faltan resúmenes de: {', '.join(pendientes)}. Usá /tarjetas.",
    }


def calcular_alertas(usuario_id: int, monto_recien: float | None = None,
                     moneda: str = MONEDA_PRINCIPAL) -> list[dict]:
    alertas: list[dict] = []
    if monto_recien is not None:
        a = alerta_gasto_grande(monto_recien, moneda)
        if a: alertas.append(a)
    for fn in (alerta_excedio_diario, alerta_proyeccion,
               alerta_innecesario_alto, alerta_resumen_tarjeta_pendiente):
        a = fn(usuario_id)
        if a: alertas.append(a)
    alertas.extend(alerta_vencimiento_cercano())
    return alertas


def formatear_alertas(alertas: list[dict]) -> str:
    if not alertas:
        return "✅ Sin alertas. Todo bajo control."
    return "\n\n".join(
        f"{a['icono']} *{a['titulo']}*\n{a['mensaje']}" for a in alertas
    )
