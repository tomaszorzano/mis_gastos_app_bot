"""
Agregación de gastos para reportes. Multi-moneda + forma de pago.
"""
from collections import defaultdict
from datetime import date, timedelta
from typing import Tuple

import storage
import ai_client
from config import (
    PERFIL_FINANCIERO,
    presupuesto_diario,
    convertir_a_principal,
    MONEDA_PRINCIPAL,
    TASA_CAMBIO,
    FORMA_PAGO_LABELS,
)


def _rango_mes_actual() -> Tuple[str, str]:
    hoy = date.today()
    return hoy.replace(day=1).isoformat(), hoy.isoformat()


def _agregar(gastos: list) -> dict:
    total_principal = 0.0
    por_moneda = defaultdict(float)
    por_cat = defaultdict(float)
    por_nec = defaultdict(float)
    por_pago = defaultdict(float)
    for g in gastos:
        m = g.get("moneda", MONEDA_PRINCIPAL)
        principal = convertir_a_principal(g["monto"], m)
        total_principal += principal
        por_moneda[m] += g["monto"]
        por_cat[g["categoria"]] += principal
        por_nec[g["necesidad"]] += principal
        por_pago[g.get("forma_pago", "efectivo")] += principal
    return {
        "n_gastos": len(gastos),
        "total_en_principal": round(total_principal, 2),
        "moneda_principal": MONEDA_PRINCIPAL,
        "tasa_cambio_usada": {k: v for k, v in TASA_CAMBIO.items() if k != MONEDA_PRINCIPAL},
        "totales_por_moneda": dict(por_moneda),
        "por_categoria": dict(sorted(por_cat.items(), key=lambda x: -x[1])),
        "por_necesidad": dict(por_nec),
        "por_forma_pago": dict(por_pago),
    }


# ----------------------------------------------------------------- #
# Resumen rápido                                                    #
# ----------------------------------------------------------------- #

def resumen_inmediato(usuario_id: int, gasto: dict) -> str:
    hoy = date.today().isoformat()
    gastos_hoy = storage.obtener_gastos(usuario_id, desde=hoy, hasta=hoy)
    total_hoy_principal = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos_hoy
    )
    limite = presupuesto_diario()
    restante = limite - total_hoy_principal
    icono = "✅" if restante >= 0 else "⚠️"
    pct = (total_hoy_principal / limite * 100) if limite else 0

    monto_str = f"{int(gasto['monto']):,} {gasto.get('moneda', MONEDA_PRINCIPAL)}"
    if gasto.get("moneda") and gasto["moneda"] != MONEDA_PRINCIPAL:
        equiv = convertir_a_principal(gasto["monto"], gasto["moneda"])
        monto_str += f" (≈ {int(equiv):,} {MONEDA_PRINCIPAL})"

    forma_pago = gasto.get("forma_pago", "efectivo")
    forma_label = FORMA_PAGO_LABELS.get(forma_pago, forma_pago)

    return (
        f"✅ Registrado: {monto_str}\n"
        f"{forma_label}\n"
        f"📂 {gasto['categoria']} ({gasto['necesidad']}) · {gasto['tipo']}\n"
        f"📝 {gasto['descripcion']}\n"
        f"🆔 {gasto['id']}\n"
        f"\n"
        f"{icono} Hoy: {int(total_hoy_principal):,} / {limite:,} {MONEDA_PRINCIPAL} ({pct:.0f}%)\n"
        f"{'Resta' if restante >= 0 else 'Sobre presupuesto'}: "
        f"{int(abs(restante)):,} {MONEDA_PRINCIPAL}"
    )


# ----------------------------------------------------------------- #
# Reportes                                                          #
# ----------------------------------------------------------------- #

def reporte_hoy(usuario_id: int) -> str:
    hoy = date.today().isoformat()
    gastos = storage.obtener_gastos(usuario_id, desde=hoy, hasta=hoy)
    agg = _agregar(gastos)
    datos = {
        "fecha": hoy,
        "limite_diario": presupuesto_diario(),
        **agg,
        "gastos": [
            {"hora": g["hora"], "monto": g["monto"], "moneda": g.get("moneda", MONEDA_PRINCIPAL),
             "forma_pago": g.get("forma_pago", "efectivo"),
             "cat": g["categoria"], "desc": g["descripcion"],
             "necesidad": g["necesidad"]}
            for g in gastos
        ],
    }
    return ai_client.analizar_reporte("hoy", datos, usuario_id)


def reporte_semana(usuario_id: int) -> str:
    hoy = date.today()
    desde = (hoy - timedelta(days=6)).isoformat()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hoy.isoformat())
    agg = _agregar(gastos)
    serie = defaultdict(float)
    for g in gastos:
        serie[g["fecha"]] += convertir_a_principal(
            g["monto"], g.get("moneda", MONEDA_PRINCIPAL)
        )
    datos = {
        "rango": f"{desde} al {hoy.isoformat()}",
        "limite_semanal": presupuesto_diario() * 7,
        "promedio_diario": agg["total_en_principal"] / 7,
        **agg,
        "serie_diaria": dict(sorted(serie.items())),
    }
    return ai_client.analizar_reporte("semana", datos, usuario_id)


def reporte_mes(usuario_id: int) -> str:
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hasta)
    resumenes = storage.obtener_resumenes_tarjeta(
        usuario_id, mes=date.today().strftime("%Y-%m")
    )
    agg = _agregar(gastos)
    presu = storage.get_presupuesto(usuario_id)
    objetivo = presu["objetivo"]
    dias_transcurridos = date.today().day
    dias_mes = 30
    proyeccion = (agg["total_en_principal"] / dias_transcurridos) * dias_mes if dias_transcurridos else 0
    pct_innecesario = (
        agg["por_necesidad"].get("innecesario", 0) / agg["total_en_principal"] * 100
        if agg["total_en_principal"] else 0
    )
    datos = {
        "rango": f"{desde} al {hasta}",
        "dias_transcurridos": dias_transcurridos,
        "presupuesto": objetivo,
        "diferencia_vs_presupuesto": objetivo - agg["total_en_principal"],
        "proyeccion_fin_de_mes": round(proyeccion),
        "pct_innecesario": round(pct_innecesario, 1),
        "objetivo_innecesario_pct": PERFIL_FINANCIERO["objetivo_innecesario_pct"],
        "resumenes_tarjeta": resumenes,
        **agg,
    }
    return ai_client.analizar_reporte("mes", datos, usuario_id)


def reporte_categorias(usuario_id: int) -> str:
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hasta)
    agg = _agregar(gastos)
    top5 = dict(list(agg["por_categoria"].items())[:5])
    datos = {"rango": f"{desde} al {hasta}", "top5_categorias": top5,
             "total_en_principal": agg["total_en_principal"],
             "totales_por_moneda": agg["totales_por_moneda"]}
    return ai_client.analizar_reporte("categoria", datos, usuario_id)


def reporte_tendencia(usuario_id: int) -> str:
    hoy = date.today()
    desde = (hoy - timedelta(days=29)).isoformat()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hoy.isoformat())
    serie = defaultdict(float)
    for g in gastos:
        serie[g["fecha"]] += convertir_a_principal(
            g["monto"], g.get("moneda", MONEDA_PRINCIPAL)
        )
    datos = {
        "rango_dias": 30,
        "serie": dict(sorted(serie.items())),
        "total_30d_principal": sum(serie.values()),
        "promedio_diario_principal": sum(serie.values()) / 30 if serie else 0,
    }
    return ai_client.analizar_reporte("tendencia", datos, usuario_id)


def reporte_proyeccion(usuario_id: int) -> str:
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hasta)
    total = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos
    )
    dias = date.today().day
    proyeccion = (total / dias) * 30 if dias else 0
    objetivo = storage.get_presupuesto(usuario_id)["objetivo"]
    datos = {
        "gastado_a_la_fecha_principal": total,
        "dias_transcurridos": dias,
        "proyeccion_fin_de_mes_principal": round(proyeccion),
        "objetivo": objetivo,
        "diferencia_proyectada": round(objetivo - proyeccion),
    }
    return ai_client.analizar_reporte("proyeccion", datos, usuario_id)


def reporte_tarjetas(usuario_id: int, mes: str | None = None) -> str:
    if mes is None:
        mes = date.today().strftime("%Y-%m")
    resumenes = storage.obtener_resumenes_tarjeta(usuario_id, mes=mes)
    if not resumenes:
        return f"No hay resúmenes registrados para {mes}.\nUsá /tarjetas para cargarlos."
    lineas = [f"💳 *Resúmenes de tarjeta — {mes}*\n"]
    total_principal = 0.0
    for r in resumenes:
        principal = convertir_a_principal(r["monto"], r["moneda"])
        total_principal += principal
        if r["moneda"] != MONEDA_PRINCIPAL:
            lineas.append(
                f"• {r['tarjeta']}: {int(r['monto']):,} {r['moneda']} "
                f"(≈ {int(principal):,} {MONEDA_PRINCIPAL})"
            )
        else:
            lineas.append(f"• {r['tarjeta']}: {int(r['monto']):,} {r['moneda']}")
    lineas.append(f"\n*Total:* {int(total_principal):,} {MONEDA_PRINCIPAL}")
    return "\n".join(lineas)


def reporte_pagos(usuario_id: int) -> str:
    """Split del mes por forma de pago (no usa Claude — texto directo)."""
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hasta)
    if not gastos:
        return "No hay gastos este mes."
    agg = _agregar(gastos)
    total = agg["total_en_principal"]
    lineas = [f"💼 *Gastos del mes por forma de pago*\n"]
    for forma, monto in sorted(agg["por_forma_pago"].items(), key=lambda x: -x[1]):
        pct = (monto / total * 100) if total else 0
        label = FORMA_PAGO_LABELS.get(forma, forma)
        lineas.append(f"{label}: {int(monto):,} {MONEDA_PRINCIPAL} ({pct:.0f}%)")
    lineas.append(f"\n*Total:* {int(total):,} {MONEDA_PRINCIPAL}")
    return "\n".join(lineas)


def conciliar_tarjeta(usuario_id: int, tarjeta: str | None = None) -> str:
    """
    Compara los gastos cargados con forma_pago=credito vs el resumen de tarjeta
    del mes actual. Útil para detectar olvidos o cargos no reconocidos.
    """
    desde, hasta = _rango_mes_actual()
    gastos = storage.obtener_gastos(usuario_id, desde=desde, hasta=hasta)
    gastos_credito = [g for g in gastos if g.get("forma_pago") == "credito"]
    total_cargado = sum(
        convertir_a_principal(g["monto"], g.get("moneda", MONEDA_PRINCIPAL))
        for g in gastos_credito
    )

    mes = date.today().strftime("%Y-%m")
    resumenes = storage.obtener_resumenes_tarjeta(usuario_id, mes=mes)
    if not resumenes:
        return (
            f"No tengo resumen de tarjeta para {mes}. "
            f"Cargalo con /tarjetas y volvé a probar /conciliar."
        )

    total_resumen = sum(
        convertir_a_principal(r["monto"], r["moneda"]) for r in resumenes
    )
    diff = total_resumen - total_cargado

    lineas = [
        f"🔍 *Conciliación — {mes}*\n",
        f"Cargaste como crédito: {int(total_cargado):,} {MONEDA_PRINCIPAL}",
        f"Resumen de tarjeta: {int(total_resumen):,} {MONEDA_PRINCIPAL}",
        f"Diferencia: {int(diff):+,} {MONEDA_PRINCIPAL}",
    ]
    if abs(diff) < 1000:
        lineas.append("\n✅ Cuadran (diferencia < 1.000).")
    elif diff > 0:
        lineas.append(
            f"\n⚠️ El resumen es mayor por {int(diff):,}. "
            f"Probable que haya gastos que no cargaste, o consumos no reconocidos. "
            f"Revisá el detalle del resumen."
        )
    else:
        lineas.append(
            f"\n⚠️ Cargaste más de lo que dice el resumen ({int(-diff):,} de más). "
            f"Posible error de carga o gastos en cuotas que aparecen en otro mes."
        )
    return "\n".join(lineas)
