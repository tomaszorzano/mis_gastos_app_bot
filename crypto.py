"""
Capa de cifrado para el archivo de datos.

Si MASTER_KEY está seteada en config, los datos se cifran con Fernet (AES-128
en modo CBC + HMAC-SHA256). Si no, se guarda como JSON plano (modo dev).

La key es responsabilidad del usuario:
- Generarla con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
- Guardarla en su password manager.
- Si la pierde, los datos se pierden. No hay recovery.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from config import MASTER_KEY

log = logging.getLogger(__name__)

_fernet = None


def _get_fernet():
    """Lazy init: solo importa cryptography si hay MASTER_KEY."""
    global _fernet
    if _fernet is not None:
        return _fernet
    if not MASTER_KEY:
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(MASTER_KEY.encode() if isinstance(MASTER_KEY, str) else MASTER_KEY)
    except Exception as e:
        raise RuntimeError(
            f"MASTER_KEY inválida. Generá una nueva con:\n"
            f"  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            f"Detalle: {e}"
        )
    return _fernet


def cifrar(data: dict) -> bytes:
    """Serializa a JSON y cifra. Si no hay key, devuelve JSON plano en bytes."""
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    f = _get_fernet()
    if f is None:
        return raw
    return f.encrypt(raw)


def descifrar(blob: bytes) -> dict:
    """Lee del archivo (cifrado o plano según haya key) y devuelve dict."""
    if not blob:
        return {}
    f = _get_fernet()
    if f is None:
        return json.loads(blob.decode("utf-8"))
    try:
        plano = f.decrypt(blob)
    except Exception as e:
        raise RuntimeError(
            f"No pude descifrar {blob[:20]!r}... ¿Cambiaste MASTER_KEY? "
            f"Si es así, los datos viejos no son recuperables. Detalle: {e}"
        )
    return json.loads(plano.decode("utf-8"))


def leer_archivo(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return descifrar(path.read_bytes())
    except RuntimeError:
        raise
    except Exception as e:
        log.error("Archivo %s ilegible: %s", path, e)
        return {}


def escribir_archivo(path: Path, data: dict) -> None:
    """Escritura atómica: tmp + rename."""
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(cifrar(data))
    os.replace(tmp, path)
