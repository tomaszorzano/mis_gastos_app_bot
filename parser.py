"""
Parsea mensajes en lenguaje natural.
Devuelve (monto, moneda, descripción).

Acepta:
  /gasto 500 supermercado
  Gasto: 1.500 combustible
  gasté 300 en netflix
  500 USD viaje
  100 usdt criptos
  u$s 50 cena
  $1500 nafta
"""
import re
from typing import Optional, Tuple

from config import MONEDAS, MONEDA_PRINCIPAL

_MONTO_RE = re.compile(
    r"""
    (?P<monto>
        \$?\s*
        \d{1,3}(?:[.,]\d{3})+
        (?:[.,]\d{1,2})?
        |
        \d+(?:[.,]\d{1,2})?
    )
    \s*(?P<k>k|K)?
    """,
    re.VERBOSE,
)

_PREFIJOS = re.compile(
    r"""^(
        /gasto\s* |
        gasto[:\s]* |
        gast[éeo]\s+(en\s+)? |
        pagu[éeo]\s+(en\s+)? |
        compr[éeo]\s+(en\s+)?
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_CONECTORES = re.compile(r"^(en|de|por|para|a)\s+", re.IGNORECASE)

# Tokens de moneda → código estándar
_MONEDA_TOKENS = [
    (re.compile(r"\bu\$s\b|\busd\b|\bd[oó]lar(es)?\b", re.IGNORECASE), "USD"),
    (re.compile(r"\busdt\b|\btether\b", re.IGNORECASE), "USDT"),
    (re.compile(r"\beur\b|\beuro(s)?\b|€", re.IGNORECASE), "EUR"),
    (re.compile(r"\bars\b|\bpesos?\b", re.IGNORECASE), "ARS"),
]


def _detectar_moneda(texto: str) -> Tuple[str, str]:
    """Devuelve (moneda, texto_sin_token_de_moneda)."""
    for patron, codigo in _MONEDA_TOKENS:
        if patron.search(texto):
            texto_limpio = patron.sub("", texto)
            return codigo, texto_limpio
    return MONEDA_PRINCIPAL, texto


def _normalizar_monto(raw: str, tiene_k: bool) -> Optional[float]:
    s = raw.replace("$", "").replace(" ", "")
    if not s:
        return None
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        partes = s.split(",")
        if len(partes[-1]) <= 2 and len(partes) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s:
        partes = s.split(".")
        if all(len(p) == 3 for p in partes[1:]) and len(partes[-1]) == 3:
            s = s.replace(".", "")
    try:
        valor = float(s)
    except ValueError:
        return None
    if tiene_k:
        valor *= 1000
    return valor


def parsear_gasto(mensaje: str) -> Optional[Tuple[float, str, str]]:
    """
    Devuelve (monto, moneda, descripción) o None.
    Si no se detecta moneda explícita, asume MONEDA_PRINCIPAL.
    """
    if not mensaje:
        return None

    texto = mensaje.strip()
    texto = _PREFIJOS.sub("", texto, count=1).strip()

    moneda, texto = _detectar_moneda(texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    match = _MONTO_RE.search(texto)
    if not match:
        return None
    monto = _normalizar_monto(match.group("monto"), bool(match.group("k")))
    if monto is None or monto <= 0:
        return None

    desc = (texto[: match.start()] + texto[match.end():]).strip()
    desc = desc.lstrip("$ \t")
    desc = _CONECTORES.sub("", desc).strip(" .,:;-")
    if not desc:
        desc = "(sin descripción)"

    return monto, moneda, desc


if __name__ == "__main__":
    casos = [
        "/gasto 500 supermercado",
        "Gasto: 1.500 combustible",
        "gasté 300 en netflix",
        "$2000 cena con amigos",
        "2k uber",
        "pague 1500,50 nafta",
        "500 USD viaje",
        "100 usdt criptos",
        "u$s 50 cena",
        "200 dolares regalo",
        "no es un gasto",
        "/hoy",
    ]
    for c in casos:
        print(f"{c!r:40} -> {parsear_gasto(c)}")
