#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gist_sync.py — RADAR by ARGOS
Sincronización de alertas con GitHub Gist (persistencia cross-deploy Railway).

Variables de entorno requeridas:
  GITHUB_GIST_TOKEN  — Personal Access Token con permiso 'gist'
  GITHUB_GIST_ID     — ID del Gist (visible en la URL)
"""

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
import json
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger("radar.gist_sync")

# ── Configuración ──────────────────────────────────────────────────────────────
GIST_TOKEN  = os.getenv("GITHUB_GIST_TOKEN", "")
GIST_ID     = os.getenv("GITHUB_GIST_ID", "")
GIST_FILE   = "alerts_db.json"
API_BASE    = "https://api.github.com"

# Cache en memoria para no martillar GitHub API en cada render del dashboard
_cache_alertas: Optional[list] = None
_cache_timestamp: float = 0
_cache_ttl: float = 60.0  # segundos

# Lock para evitar race conditions cuando el monitor escribe en paralelo
_sync_lock = threading.Lock()


def _headers() -> dict:
    return {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def _esta_configurado() -> bool:
    """Verifica que las variables de entorno estén presentes."""
    return bool(GIST_TOKEN and GIST_ID)


def _inferir_source_type(alert_dict: dict) -> str:
    """Normaliza el source_type de una alerta para filtros del dashboard."""
    if alert_dict.get("source_type"):
        return alert_dict["source_type"]

    msg  = (alert_dict.get("mensaje") or "").lower()
    hit  = alert_dict.get("hit") or {}
    url  = (hit.get("url") or alert_dict.get("url") or "").lower()

    if "sugef" in msg and ("sanc" in msg or "sancio" in msg):
        return "sugef_sanciones"
    if "sugef" in msg or "conassif" in msg:
        return "sugef_doc"
    if "gaceta" in msg:
        return "gaceta"
    if "tweet" in msg or "twitter" in msg or "x.com" in url or "twitter.com" in url:
        return "twitter"
    if "artículo" in msg or "artículo detectado" in msg or "nacion" in url or "crhoy" in url:
        return "medios_cr"
    if "cluster" in alert_dict or alert_dict.get("cluster"):
        return "keywords_exa"
    return "otros"


def _descargar_gist() -> dict:
    """Descarga el contenido actual del Gist. Devuelve el dict del JSON."""
    resp = requests.get(
        f"{API_BASE}/gists/{GIST_ID}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    gist_data = resp.json()
    raw_content = gist_data["files"][GIST_FILE]["content"]
    return json.loads(raw_content)


def _subir_gist(db: dict) -> None:
    """Sube el dict actualizado al Gist."""
    payload = {
        "files": {
            GIST_FILE: {
                "content": json.dumps(db, indent=2, ensure_ascii=False)
            }
        }
    }
    resp = requests.patch(
        f"{API_BASE}/gists/{GIST_ID}",
        headers=_headers(),
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()


def sync_alert_to_gist(alert_dict: dict) -> bool:
    """
    Agrega una alerta al Gist de forma thread-safe.
    Devuelve True si tuvo éxito, False si falló (silencioso).
    """
    if not _esta_configurado():
        logger.debug("Gist no configurado (GITHUB_GIST_TOKEN / GITHUB_GIST_ID vacíos)")
        return False

    with _sync_lock:
        try:
            db = _descargar_gist()

            alert_normalizada = {
                "timestamp":   alert_dict.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "criticidad":  alert_dict.get("criticidad", "NORMAL"),
                "mensaje":     alert_dict.get("mensaje", ""),
                "source_type": _inferir_source_type(alert_dict),
                "cluster":     alert_dict.get("cluster"),
                "score":       alert_dict.get("score", 0),
                "cliente":     alert_dict.get("cliente", "promerica_cr"),
                "caso_id":     alert_dict.get("caso_id", "ARGOS-2026-001"),
                "hit":         alert_dict.get("hit") or {},
            }

            db.setdefault("alerts", [])
            db["alerts"].append(alert_normalizada)
            db["updated_at"] = datetime.now(timezone.utc).isoformat()
            db["total"] = len(db["alerts"])

            _subir_gist(db)
            logger.info(f"✅ Gist sync OK — total alertas: {db['total']}")

            global _cache_alertas, _cache_timestamp
            _cache_alertas = None
            _cache_timestamp = 0

            return True

        except Exception as e:
            logger.warning(f"Gist sync falló (no crítico): {e}")
            return False


def cargar_alertas_gist(force_refresh: bool = False) -> list:
    """
    Descarga y parsea las alertas del Gist.
    Usa cache de 60 segundos para no martillar GitHub API.
    """
    global _cache_alertas, _cache_timestamp

    if not _esta_configurado():
        return []

    ahora = time.time()
    if not force_refresh and _cache_alertas is not None and (ahora - _cache_timestamp) < _cache_ttl:
        return _cache_alertas

    try:
        db = _descargar_gist()
        _cache_alertas = db.get("alerts", [])
        _cache_timestamp = ahora
        return _cache_alertas
    except Exception as e:
        logger.warning(f"Error cargando alertas desde Gist: {e}")
        return _cache_alertas or []


def estado_gist() -> dict:
    """Devuelve información de estado del Gist para la sección Config del dashboard."""
    if not _esta_configurado():
        return {"configurado": False, "error": "Variables GITHUB_GIST_TOKEN / GITHUB_GIST_ID no configuradas"}

    try:
        db = _descargar_gist()
        return {
            "configurado":  True,
            "total":        db.get("total", len(db.get("alerts", []))),
            "updated_at":   db.get("updated_at", "—"),
            "gist_id":      GIST_ID[:8] + "...",
        }
    except Exception as e:
        return {"configurado": True, "error": str(e)}
