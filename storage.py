"""
Persistencia. Usa crypto.py para leer/escribir el archivo (cifrado si hay
MASTER_KEY).

Schema:
{
  "<usuario_id>": {
    "usuario_id": int,
    "creado": iso,
    "chat_id": int,
    "gastos": [{
      id, fecha, hora, monto, moneda, categoria, tipo, necesidad,
      urgencia, descripcion, nota, forma_pago
    }],
    "resumenes_tarjeta": [{id, fecha_registro, mes, tarjeta, monto, moneda, nota}],
    "dias_sin_gastos": ["YYYY-MM-DD", ...],   # registrados explícitamente
    "presupuesto": {mes_actual, objetivo}
  }
}
"""
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import crypto
from config import DATA_FILE, PERFIL_FINANCIERO, MONEDA_PRINCIPAL


def _ahora_iso() -> dict:
    n = datetime.now()
    return {"fecha": n.date().isoformat(), "hora": n.time().strftime("%H:%M:%S")}


def _estructura_inicial(usuario_id: int) -> dict:
    return {
        "usuario_id": usuario_id,
        "creado": datetime.now().isoformat(),
        "chat_id": usuario_id,
        "gastos": [],
        "resumenes_tarjeta": [],
        "dias_sin_gastos": [],
        "presupuesto": {
            "mes_actual": date.today().strftime("%Y-%m"),
            "objetivo": PERFIL_FINANCIERO["presupuesto_mes_actual"],
        },
    }


def _cargar_todo() -> dict:
    return crypto.leer_archivo(DATA_FILE)


def _guardar_todo(data: dict) -> None:
    crypto.escribir_archivo(DATA_FILE, data)


def _migrar(datos: dict) -> dict:
    datos.setdefault("resumenes_tarjeta", [])
    datos.setdefault("dias_sin_gastos", [])
    datos.setdefault("chat_id", datos.get("usuario_id"))
    for g in datos.get("gastos", []):
        g.setdefault("moneda", MONEDA_PRINCIPAL)
        g.setdefault("forma_pago", "efectivo")
    return datos


def _datos_usuario(usuario_id: int) -> dict:
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
        _guardar_todo(todo)
    todo[key] = _migrar(todo[key])
    return todo[key]


def set_chat_id(usuario_id: int, chat_id: int) -> None:
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
    todo[key]["chat_id"] = chat_id
    _guardar_todo(todo)


def get_chat_id(usuario_id: int) -> Optional[int]:
    todo = _cargar_todo()
    return todo.get(str(usuario_id), {}).get("chat_id")


def listar_usuarios() -> list[int]:
    return [int(k) for k in _cargar_todo().keys()]


# ----------------------------------------------------------------- #
# Gastos                                                            #
# ----------------------------------------------------------------- #

def guardar_gasto(usuario_id: int, monto: float, descripcion: str,
                  categorizacion: dict, moneda: str = MONEDA_PRINCIPAL,
                  forma_pago: str = "efectivo", nota: str = "") -> dict:
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
    todo[key] = _migrar(todo[key])

    timestamp = _ahora_iso()
    gasto = {
        "id": str(uuid.uuid4())[:8],
        "fecha": timestamp["fecha"],
        "hora": timestamp["hora"],
        "monto": float(monto),
        "moneda": moneda,
        "forma_pago": forma_pago,
        "categoria": categorizacion.get("categoria", "Otros"),
        "tipo": categorizacion.get("tipo", "variable"),
        "necesidad": categorizacion.get("necesidad", "importante"),
        "urgencia": categorizacion.get("urgencia", "media"),
        "descripcion": descripcion,
        "nota": nota,
    }
    todo[key]["gastos"].append(gasto)
    # Si había marcado el día como "sin gastos", lo sacamos
    if timestamp["fecha"] in todo[key].get("dias_sin_gastos", []):
        todo[key]["dias_sin_gastos"].remove(timestamp["fecha"])
    _guardar_todo(todo)
    return gasto


def obtener_gastos(usuario_id: int, desde: Optional[str] = None,
                   hasta: Optional[str] = None) -> list:
    datos = _datos_usuario(usuario_id)
    gastos = datos["gastos"]
    if desde:
        gastos = [g for g in gastos if g["fecha"] >= desde]
    if hasta:
        gastos = [g for g in gastos if g["fecha"] <= hasta]
    return gastos


def obtener_gasto_por_id(usuario_id: int, gasto_id: str) -> Optional[dict]:
    for g in _datos_usuario(usuario_id)["gastos"]:
        if g["id"] == gasto_id:
            return g
    return None


def actualizar_gasto(usuario_id: int, gasto_id: str, cambios: dict) -> Optional[dict]:
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        return None
    for g in todo[key]["gastos"]:
        if g["id"] == gasto_id:
            g.update(cambios)
            _guardar_todo(todo)
            return g
    return None


def eliminar_gasto(usuario_id: int, gasto_id: str) -> bool:
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        return False
    antes = len(todo[key]["gastos"])
    todo[key]["gastos"] = [g for g in todo[key]["gastos"] if g["id"] != gasto_id]
    if len(todo[key]["gastos"]) < antes:
        _guardar_todo(todo)
        return True
    return False


# ----------------------------------------------------------------- #
# Días sin gastos (registrados desde recordatorio nocturno)         #
# ----------------------------------------------------------------- #

def marcar_dia_sin_gastos(usuario_id: int, fecha_iso: Optional[str] = None) -> None:
    if fecha_iso is None:
        fecha_iso = date.today().isoformat()
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
    todo[key] = _migrar(todo[key])
    if fecha_iso not in todo[key]["dias_sin_gastos"]:
        todo[key]["dias_sin_gastos"].append(fecha_iso)
        _guardar_todo(todo)


def es_dia_sin_gastos(usuario_id: int, fecha_iso: str) -> bool:
    return fecha_iso in _datos_usuario(usuario_id).get("dias_sin_gastos", [])


def tiene_actividad_hoy(usuario_id: int) -> bool:
    """¿Hay gastos cargados hoy o el usuario marcó el día como sin gastos?"""
    hoy = date.today().isoformat()
    if es_dia_sin_gastos(usuario_id, hoy):
        return True
    return len(obtener_gastos(usuario_id, hoy, hoy)) > 0


# ----------------------------------------------------------------- #
# Resúmenes de tarjeta                                              #
# ----------------------------------------------------------------- #

def guardar_resumen_tarjeta(usuario_id: int, tarjeta: str, monto: float,
                            moneda: str = MONEDA_PRINCIPAL,
                            mes: Optional[str] = None,
                            nota: str = "") -> dict:
    if mes is None:
        mes = date.today().strftime("%Y-%m")
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
    todo[key] = _migrar(todo[key])

    existente = next(
        (r for r in todo[key]["resumenes_tarjeta"]
         if r["tarjeta"] == tarjeta and r["mes"] == mes),
        None,
    )
    if existente:
        existente.update({
            "monto": float(monto), "moneda": moneda, "nota": nota,
            "fecha_registro": date.today().isoformat(),
        })
        registro = existente
    else:
        registro = {
            "id": str(uuid.uuid4())[:8],
            "fecha_registro": date.today().isoformat(),
            "mes": mes,
            "tarjeta": tarjeta,
            "monto": float(monto),
            "moneda": moneda,
            "nota": nota,
        }
        todo[key]["resumenes_tarjeta"].append(registro)
    _guardar_todo(todo)
    return registro


def obtener_resumenes_tarjeta(usuario_id: int,
                              mes: Optional[str] = None) -> list:
    datos = _datos_usuario(usuario_id)
    resumenes = datos.get("resumenes_tarjeta", [])
    if mes:
        resumenes = [r for r in resumenes if r["mes"] == mes]
    return resumenes


def ya_registrado_mes(usuario_id: int, tarjeta: str, mes: str) -> bool:
    return any(
        r["tarjeta"] == tarjeta and r["mes"] == mes
        for r in obtener_resumenes_tarjeta(usuario_id, mes)
    )


def tarjetas_pendientes_del_mes(usuario_id: int, tarjetas: list[str],
                                mes: Optional[str] = None) -> list[str]:
    if mes is None:
        mes = date.today().strftime("%Y-%m")
    return [t for t in tarjetas if not ya_registrado_mes(usuario_id, t, mes)]


# ----------------------------------------------------------------- #
# Presupuesto / utilidades                                          #
# ----------------------------------------------------------------- #

def set_presupuesto(usuario_id: int, monto: int) -> None:
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
    todo[key]["presupuesto"]["objetivo"] = int(monto)
    todo[key]["presupuesto"]["mes_actual"] = date.today().strftime("%Y-%m")
    _guardar_todo(todo)


def get_presupuesto(usuario_id: int) -> dict:
    return _datos_usuario(usuario_id)["presupuesto"]


def reset_usuario(usuario_id: int) -> None:
    todo = _cargar_todo()
    todo[str(usuario_id)] = _estructura_inicial(usuario_id)
    _guardar_todo(todo)


def exportar_csv(usuario_id: int) -> Path:
    import csv
    gastos = obtener_gastos(usuario_id)
    out = DATA_FILE.parent / f"export_{usuario_id}_{date.today().isoformat()}.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        if not gastos:
            f.write("No hay gastos registrados\n")
            return out
        writer = csv.DictWriter(f, fieldnames=list(gastos[0].keys()))
        writer.writeheader()
        writer.writerows(gastos)
    return out


# ----------------------------------------------------------------- #
# Perfil financiero por usuario (v2)                                 #
# ----------------------------------------------------------------- #

def _perfil_default() -> dict:
    """Perfil vacío que el usuario completa con /setup."""
    return {
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
        "tarjetas_trackear": [],
        "hora_recordatorio": 22,
        "plan": "",
        "moneda_principal": MONEDA_PRINCIPAL,
    }


def obtener_perfil(usuario_id: int) -> dict:
    """Devuelve perfil del usuario. Si no existe, devuelve default vacío."""
    datos = _datos_usuario(usuario_id)
    if "perfil" not in datos:
        datos["perfil"] = _perfil_default()
        # Persistir el cambio
        todo = _cargar_todo()
        todo[str(usuario_id)] = datos
        _guardar_todo(todo)
    return datos["perfil"]


def guardar_perfil(usuario_id: int, perfil: dict) -> None:
    """Reemplaza completamente el perfil del usuario."""
    todo = _cargar_todo()
    key = str(usuario_id)
    if key not in todo:
        todo[key] = _estructura_inicial(usuario_id)
    todo[key] = _migrar(todo[key])
    perfil["configurado"] = True
    todo[key]["perfil"] = perfil
    # Sincronizar presupuesto
    todo[key]["presupuesto"]["objetivo"] = perfil.get("presupuesto_mes_actual", 0)
    _guardar_todo(todo)


def actualizar_objetivo(usuario_id: int, campo: str, valor) -> dict:
    """Actualiza un campo específico de los objetivos."""
    perfil = obtener_perfil(usuario_id)
    if campo == "presupuesto_mes_actual" or campo == "ingreso_mensual":
        perfil[campo] = valor
    else:
        perfil["objetivos"][campo] = valor
    guardar_perfil(usuario_id, perfil)
    return perfil


def agregar_deuda(usuario_id: int, deuda: dict) -> dict:
    perfil = obtener_perfil(usuario_id)
    if "id" not in deuda:
        deuda["id"] = str(uuid.uuid4())[:8]
    perfil["deudas"].append(deuda)
    guardar_perfil(usuario_id, perfil)
    return deuda


def eliminar_deuda(usuario_id: int, deuda_id: str) -> bool:
    perfil = obtener_perfil(usuario_id)
    antes = len(perfil["deudas"])
    perfil["deudas"] = [d for d in perfil["deudas"] if d.get("id") != deuda_id]
    if len(perfil["deudas"]) < antes:
        guardar_perfil(usuario_id, perfil)
        return True
    return False


def actualizar_deuda(usuario_id: int, deuda_id: str, cambios: dict) -> Optional[dict]:
    perfil = obtener_perfil(usuario_id)
    for d in perfil["deudas"]:
        if d.get("id") == deuda_id:
            d.update(cambios)
            guardar_perfil(usuario_id, perfil)
            return d
    return None


# ----------------------------------------------------------------- #
# Sistema de aprobación de usuarios (v2)                            #
# ----------------------------------------------------------------- #
# Estructura especial bajo la clave "_admin" del archivo principal:
# {
#   "_admin": {
#     "pendientes": [{"user_id", "username", "first_name", "fecha", "chat_id"}],
#     "rechazados": [user_id, ...]
#   }
# }

def _admin_data() -> dict:
    todo = _cargar_todo()
    if "_admin" not in todo:
        todo["_admin"] = {"pendientes": [], "rechazados": []}
        _guardar_todo(todo)
    return todo["_admin"]


def _save_admin_data(admin: dict) -> None:
    todo = _cargar_todo()
    todo["_admin"] = admin
    _guardar_todo(todo)


def solicitar_acceso(user_id: int, username: str, first_name: str,
                     chat_id: int) -> bool:
    """
    Registra una solicitud de acceso. Devuelve True si se agregó (nueva),
    False si ya estaba pendiente o rechazado.
    """
    admin = _admin_data()
    if any(p["user_id"] == user_id for p in admin["pendientes"]):
        return False
    if user_id in admin["rechazados"]:
        return False
    admin["pendientes"].append({
        "user_id": user_id,
        "username": username or "",
        "first_name": first_name or "",
        "fecha": datetime.now().isoformat(),
        "chat_id": chat_id,
    })
    _save_admin_data(admin)
    return True


def listar_pendientes() -> list:
    return _admin_data()["pendientes"]


def aprobar_usuario(user_id: int) -> Optional[dict]:
    """
    Saca al user de pendientes. Devuelve los datos para que el caller
    avise al usuario. NO modifica ALLOWED_USER_IDS (eso es env var, manual).
    """
    admin = _admin_data()
    pendiente = next((p for p in admin["pendientes"] if p["user_id"] == user_id), None)
    if not pendiente:
        return None
    admin["pendientes"] = [p for p in admin["pendientes"] if p["user_id"] != user_id]
    _save_admin_data(admin)
    return pendiente


def rechazar_usuario(user_id: int) -> Optional[dict]:
    admin = _admin_data()
    pendiente = next((p for p in admin["pendientes"] if p["user_id"] == user_id), None)
    if not pendiente:
        return None
    admin["pendientes"] = [p for p in admin["pendientes"] if p["user_id"] != user_id]
    if user_id not in admin["rechazados"]:
        admin["rechazados"].append(user_id)
    _save_admin_data(admin)
    return pendiente


def stats_globales() -> dict:
    """Stats agregadas SIN exponer datos individuales (privacidad)."""
    todo = _cargar_todo()
    usuarios = [k for k in todo.keys() if k != "_admin"]
    return {
        "total_usuarios": len(usuarios),
        "usuarios_configurados": sum(
            1 for k in usuarios
            if todo[k].get("perfil", {}).get("configurado", False)
        ),
        "usuarios_pendientes": len(_admin_data()["pendientes"]),
    }

