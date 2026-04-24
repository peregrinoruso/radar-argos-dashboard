#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar_dashboard.py — RADAR by ARGOS
Dashboard Streamlit de monitoreo de crisis reputacionales.

Uso:
    streamlit run radar_dashboard.py

Variables de entorno opcionales:
    GITHUB_GIST_ID      — ID del Gist con historial de alertas
    GITHUB_GIST_TOKEN   — Token GitHub (lectura)
    RADAR_ALERTS_DIR    — Ruta a carpeta alerts/ local (fallback)
"""

import sys
import os

# Asegurar que gist_sync.py sea importable desde cualquier directorio de ejecución
# (necesario cuando Streamlit Cloud corre desde la raíz del repo)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass  # No disponible en todos los entornos (ej. Streamlit Cloud)

import json
import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# ── Configuración de página ────────────────────────────────────────────────────
st.set_page_config(
    page_title="RADAR by ARGOS",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Paleta de colores ARGOS ────────────────────────────────────────────────────
COLORES = {
    "CRÍTICA":  "#C0392B",
    "ALTA":     "#F06A1A",
    "MEDIA":    "#E8A020",
    "BAJA":     "#2E86AB",
    "NORMAL":   "#2E86AB",
    "fondo":    "#0D0D14",
    "texto":    "#F0F2F5",
    "acento":   "#E8A020",
}

EMOJIS_CRITICIDAD = {
    "CRÍTICA": "🚨",
    "ALTA":    "🔴",
    "MEDIA":   "🟡",
    "BAJA":    "🟢",
    "NORMAL":  "🔵",
}

CSS = """
<style>
    .radar-header {
        background: linear-gradient(135deg, #0D0D14, #1A1A2E);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        border-left: 4px solid #E8A020;
        margin-bottom: 1.5rem;
    }
    .radar-title {
        font-size: 2rem;
        font-weight: 700;
        color: #F0F2F5;
        font-family: Arial, sans-serif;
        margin: 0;
    }
    .radar-subtitle {
        color: #E8A020;
        font-size: 0.9rem;
        margin-top: 0.3rem;
        font-family: Arial, sans-serif;
    }
    .semaforo-box {
        text-align: center;
        padding: 1.5rem;
        border-radius: 12px;
        margin: 1rem 0;
    }
    .semaforo-rojo   { background: #2d0a0a; border: 2px solid #C0392B; }
    .semaforo-naranja { background: #2d1a0a; border: 2px solid #F06A1A; }
    .semaforo-verde  { background: #0a2d0a; border: 2px solid #27AE60; }
    .semaforo-gris   { background: #1a1a1a; border: 2px solid #555; }
    .semaforo-icon { font-size: 3rem; }
    .semaforo-label { font-size: 1.2rem; font-weight: 700; color: #F0F2F5; margin-top: 0.5rem; }
    .semaforo-sub   { font-size: 0.8rem; color: #aaa; }
    .metric-card {
        background: #1A1A2E;
        border-radius: 8px;
        padding: 1rem 1.5rem;
        border-top: 3px solid #E8A020;
        text-align: center;
    }
    .alert-critica { border-left: 4px solid #C0392B; padding-left: 8px; }
    .alert-alta    { border-left: 4px solid #F06A1A; padding-left: 8px; }
    .alert-media   { border-left: 4px solid #E8A020; padding-left: 8px; }
    .alert-baja    { border-left: 4px solid #2E86AB; padding-left: 8px; }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


# ── Carga de datos ─────────────────────────────────────────────────────────────

def _directorio_alertas_local() -> str:
    custom = os.getenv("RADAR_ALERTS_DIR", "")
    if custom and os.path.isdir(custom):
        return custom
    # Auto-detectar: mismo directorio que este script
    script_dir = Path(__file__).parent
    return str(script_dir / "alerts")


# Solo mostrar alertas a partir de esta fecha — evita ruido histórico
FECHA_LIMITE_DASHBOARD = "2026-04-22"

@st.cache_data(ttl=120)
def cargar_alertas() -> list[dict]:
    """Carga alertas del Gist y/o carpeta local. Dedup por timestamp.
    Aplica filtro de fecha: solo alertas >= FECHA_LIMITE_DASHBOARD."""
    alertas = []
    fuentes_ok = []

    # 1. Intentar Gist
    try:
        from gist_sync import cargar_alertas_gist
        gist_alerts = cargar_alertas_gist()
        if gist_alerts:
            alertas.extend(gist_alerts)
            fuentes_ok.append(f"Gist ({len(gist_alerts)} alertas)")
    except Exception:
        pass

    # 2. Fallback: archivos locales
    alerts_dir = _directorio_alertas_local()
    if os.path.isdir(alerts_dir):
        archivos = sorted(glob.glob(f"{alerts_dir}/*.json"))
        ts_en_gist = {a.get("timestamp") for a in alertas}
        locales = 0
        for archivo in archivos:
            try:
                with open(archivo, encoding="utf-8") as f:
                    a = json.load(f)
                ts = a.get("timestamp", "")
                if ts not in ts_en_gist:
                    a.setdefault("source_type", _inferir_local(a))
                    a.setdefault("cluster", None)
                    a.setdefault("score", 0)
                    alertas.append(a)
                    ts_en_gist.add(ts)
                    locales += 1
            except Exception:
                pass
        if locales:
            fuentes_ok.append(f"Local ({locales} alertas)")

    # Filtro de fecha: solo alertas a partir del 22 de abril de 2026
    alertas = [
        a for a in alertas
        if a.get("timestamp", "")[:10] >= FECHA_LIMITE_DASHBOARD
    ]

    # Ordenar más reciente primero
    alertas.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
    return alertas


def _inferir_local(alert: dict) -> str:
    msg = (alert.get("mensaje") or "").lower()
    if "sugef" in msg and "sanc" in msg:
        return "sugef_sanciones"
    if "sugef" in msg or "conassif" in msg:
        return "sugef_doc"
    if "tweet" in msg or "twitter" in msg:
        return "twitter"
    if "artículo" in msg:
        return "medios_cr"
    if alert.get("cluster"):
        return "keywords_exa"
    return "otros"


def calcular_semaforo(alertas: list, ventana_horas: int = 6) -> tuple[str, str]:
    """
    Devuelve (estado, descripción) basado en alertas recientes.
    estados: ROJO / NARANJA / VERDE / GRIS
    """
    if not alertas:
        return "GRIS", "Sin datos"

    ahora = datetime.now(timezone.utc)
    limite = ahora - timedelta(hours=ventana_horas)

    recientes = []
    for a in alertas:
        try:
            ts = datetime.fromisoformat(a["timestamp"].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= limite:
                recientes.append(a)
        except Exception:
            pass

    if not recientes:
        return "VERDE", f"Sin alertas en las últimas {ventana_horas}h"

    criticidades = [a.get("criticidad", "NORMAL") for a in recientes]
    if "CRÍTICA" in criticidades:
        return "ROJO", f"{criticidades.count('CRÍTICA')} alerta(s) CRÍTICA(s) en las últimas {ventana_horas}h"
    if "ALTA" in criticidades:
        return "NARANJA", f"{criticidades.count('ALTA')} alerta(s) ALTA(s) en las últimas {ventana_horas}h"
    return "VERDE", f"{len(recientes)} alerta(s) de baja criticidad en las últimas {ventana_horas}h"


def render_semaforo(estado: str, descripcion: str):
    iconos = {"ROJO": "🔴", "NARANJA": "🟠", "VERDE": "🟢", "GRIS": "⚫"}
    labels = {"ROJO": "ALERTA CRÍTICA", "NARANJA": "ATENCIÓN", "VERDE": "NORMAL", "GRIS": "SIN DATOS"}
    clases = {"ROJO": "semaforo-rojo", "NARANJA": "semaforo-naranja", "VERDE": "semaforo-verde", "GRIS": "semaforo-gris"}

    st.markdown(f"""
    <div class="semaforo-box {clases.get(estado, 'semaforo-gris')}">
        <div class="semaforo-icon">{iconos.get(estado, '⚫')}</div>
        <div class="semaforo-label">{labels.get(estado, estado)}</div>
        <div class="semaforo-sub">{descripcion}</div>
    </div>
    """, unsafe_allow_html=True)


def alertas_a_df(alertas: list) -> pd.DataFrame:
    rows = []
    for a in alertas:
        hit = a.get("hit") or {}
        rows.append({
            "timestamp":   a.get("timestamp", ""),
            "criticidad":  a.get("criticidad", "NORMAL"),
            "source_type": a.get("source_type", "otros"),
            "cluster":     a.get("cluster", "—"),
            "score":       a.get("score", 0),
            "mensaje":     a.get("mensaje", ""),
            "url":         str(hit.get("url") or a.get("url") or "").strip(),
            "titulo":      hit.get("title") or hit.get("titulo", ""),
            "usuario":     hit.get("usuario", ""),
            "retweets":    hit.get("retweets", 0),
            "likes":       hit.get("likes", 0),
        })
    df = pd.DataFrame(rows)
    if not df.empty and "timestamp" in df.columns:
        df["ts_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df["fecha"]  = df["ts_dt"].dt.strftime("%Y-%m-%d")
        df["hora"]   = df["ts_dt"].dt.strftime("%H:%M")
    return df


# ── Páginas del dashboard ──────────────────────────────────────────────────────

FUENTE_LABELS = {
    "sugef_sanciones": "🏛️ SUGEF Sanciones",
    "sugef_doc":       "📄 SUGEF Documentos",
    "medios_cr":       "📰 Medios de comunicación",
    "twitter":         "🐦 Twitter / X",
    "keywords_exa":    "🔍 Búsqueda web (Exa)",
    "gaceta":          "📋 Gaceta Oficial",
    "conassif":        "🏦 CONASSIF",
    "linkedin":        "💼 LinkedIn",
    "reddit":          "🔴 Reddit",
    "blogs_foros":     "💬 Blogs y foros",
    "google_news":     "📰 Google News RSS",
    "facebook":        "📘 Facebook",
    "tiktok":          "🎵 TikTok",
    "youtube":         "▶️ YouTube",
    "otros":           "⚪ Sin clasificar",
}

FUENTE_DESCRIPCIONES = {
    "sugef_sanciones": "Cambios en la página oficial de sanciones de SUGEF. Alta relevancia regulatoria.",
    "sugef_doc":       "Circulares, resoluciones y documentos nuevos publicados por SUGEF.",
    "medios_cr":       "Artículos en medios costarricenses (El Financiero, CRHoy, Semanario, etc.).",
    "twitter":         "Tweets sobre Promerica/SUGEF con menciones verificadas. Filtro estricto aplicado.",
    "keywords_exa":    "Búsqueda profunda web via Exa — 7 clusters de keywords (35+ queries).",
    "gaceta":          "Publicaciones en La Gaceta Oficial de Costa Rica (cubierta via Google News).",
    "conassif":        "Documentos o cambios detectados en el sitio del CONASSIF.",
    "linkedin":        "Posts y artículos de profesionales en LinkedIn sobre Promerica/SUGEF.",
    "reddit":          "Menciones en Reddit — foros de finanzas y comunidades CR.",
    "blogs_foros":     "Blogs financieros, foros de consumidores y sitios de opinión.",
    "google_news":     "Agregador Google News — captura La Nación, Extra, AP y 50+ fuentes.",
    "facebook":        "Publicaciones en páginas de medios costarricenses en Facebook.",
    "tiktok":          "Videos TikTok con menciones de Promerica/SUGEF.",
    "youtube":         "Videos YouTube sobre la sanción Promerica.",
    "otros":           "Alertas sin fuente identificada o de fuentes nuevas.",
}

FUENTE_COLORES = {
    "🏛️ SUGEF Sanciones":        "#C0392B",
    "📄 SUGEF Documentos":        "#E8A020",
    "📰 Medios de comunicación":  "#2980B9",
    "🐦 Twitter / X":             "#1DA1F2",
    "🔍 Búsqueda web (Exa)":      "#27AE60",
    "📋 Gaceta Oficial":          "#8E44AD",
    "🏦 CONASSIF":                "#16A085",
    "💼 LinkedIn":                "#0077B5",
    "🔴 Reddit":                  "#FF4500",
    "💬 Blogs y foros":           "#F39C12",
    "📰 Google News RSS":         "#4285F4",
    "📘 Facebook":                "#1877F2",
    "🎵 TikTok":                  "#010101",
    "▶️ YouTube":                  "#FF0000",
    "⚪ Sin clasificar":           "#555555",
}

COLOR_CRITICIDAD = {
    "CRÍTICA": "#C0392B",
    "ALTA":    "#F06A1A",
    "MEDIA":   "#E8A020",
    "NORMAL":  "#2E86AB",
    "BAJA":    "#27AE60",
}


def _get_url(a: dict) -> str:
    """Extrae la URL de una alerta independientemente de su estructura.
    Normaliza URLs sin protocolo agregando https://.
    """
    hit = a.get("hit") or {}
    url = a.get("url") or hit.get("url") or a.get("url_final") or ""
    if not url or url in ("N/A", "n/a", "—", "-", "null", "None"):
        return ""
    url = str(url).strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def _render_alertas_filtradas(alertas_filtradas: list, titulo: str):
    """Muestra alertas en tarjetas expandibles con toda la información disponible."""
    st.markdown(f"#### {titulo} ({len(alertas_filtradas)} alertas)")
    if not alertas_filtradas:
        st.info("No hay alertas para esta selección.")
        return

    for a in alertas_filtradas[:20]:
        crit   = a.get("criticidad", "NORMAL")
        emoji  = EMOJIS_CRITICIDAD.get(crit, "•")
        ts     = str(a.get("timestamp", ""))[:16].replace("T", " ")
        msg    = str(a.get("mensaje", ""))
        url    = _get_url(a)
        fuente = FUENTE_LABELS.get(a.get("source_type", "otros"), a.get("source_type", ""))

        # Título del expander: más corto, con ícono de link si tiene URL
        link_icon = " 🔗" if url else ""
        with st.expander(f"{emoji} `{ts}` — **{crit}** · {msg[:65]}{link_icon}", expanded=False):

            # Botón grande de acceso a la fuente (si hay URL)
            if url:
                st.link_button("🌐 Abrir fuente original", url, use_container_width=True)
            else:
                st.caption("⚠️ Esta alerta no tiene URL registrada (generada por cambio de hash o evento interno).")

            st.divider()

            # Datos según tipo de alerta
            # — Sanción SUGEF (tiene entidad, monto, motivo)
            entidad = a.get("entidad", "")
            monto   = a.get("monto_colones", "")
            motivo  = a.get("motivo", "")
            norma   = a.get("norma", "")
            accion  = a.get("accion_requerida", "")
            if entidad or monto:
                st.markdown("##### 🏛️ Datos de la sanción SUGEF")
                col1, col2 = st.columns(2)
                with col1:
                    if entidad: st.markdown(f"**Entidad**: {entidad}")
                    if a.get("tipo_sancion"): st.markdown(f"**Tipo**: {a['tipo_sancion']}")
                    if a.get("fecha_firmeza"): st.markdown(f"**Fecha firmeza**: {a['fecha_firmeza']}")
                with col2:
                    if monto: st.markdown(f"**Monto**: `{monto}`")
                    if a.get("monto_usd_aprox"): st.markdown(f"**En USD**: `{a['monto_usd_aprox']}`")
                if motivo: st.markdown(f"**Motivo**: {motivo}")
                if norma:  st.markdown(f"**Norma infringida**: {norma}")
                if accion:
                    st.warning(f"⚡ **Acción requerida**: {accion}")
                st.divider()

            # — Artículo de medios (tiene titular, medio, keywords_match)
            titular = a.get("titular", "")
            medio   = a.get("medio", "")
            kw      = a.get("keywords_match", "")
            if titular or medio:
                st.markdown("##### 📰 Artículo detectado")
                if titular: st.markdown(f"**Titular**: *{titular}*")
                if medio:   st.markdown(f"**Medio**: `{medio.upper()}`")
                if kw:      st.markdown(f"**Keyword que lo activó**: `{kw}`")
                st.divider()

            # — Documento SUGEF (tiene fuente, sha256, archivo)
            sha = a.get("sha256", "")
            fuente_doc = a.get("fuente", "")
            if sha:
                st.markdown("##### 📄 Documento regulatorio")
                if fuente_doc: st.markdown(f"**Publicado por**: {fuente_doc}")
                st.caption(f"SHA-256: `{sha[:16]}…`")
                st.divider()

            # — Twitter hit (soporta formato viejo {hit: {}} y nuevo {usuario: ...})
            hit = a.get("hit") or {}
            usuario_tw = hit.get("usuario") or a.get("usuario", "")
            texto_tw   = hit.get("text") or hit.get("texto") or a.get("texto", "")
            rt_tw      = hit.get("retweets", 0) or a.get("retweets", 0)
            likes_tw   = hit.get("likes", 0) or a.get("likes", 0)
            seg_tw     = a.get("seguidores", 0)
            if usuario_tw:
                st.markdown("##### 🐦 Tweet detectado")
                seg_str = f" · {seg_tw:,} seguidores" if seg_tw else ""
                st.markdown(f"**{usuario_tw}**{seg_str} — 🔁 {rt_tw} ❤️ {likes_tw}")
                if texto_tw:
                    st.text(texto_tw[:300])
                st.divider()

            # — LinkedIn / blogs / foros
            titular = a.get("titular", "") or a.get("titulo", "")
            preview  = a.get("preview", "")
            source_t = a.get("source_type", "")
            if source_t in ("linkedin", "reddit", "blogs_foros", "google_news") and not usuario_tw:
                if titular:
                    st.markdown(f"##### Titular: *{titular}*")
                if preview:
                    st.caption(preview[:300])
                st.divider()

            # Metadatos generales
            col1, col2, col3 = st.columns(3)
            col1.markdown(f"**Criticidad**: `{crit}`")
            col2.markdown(f"**Fuente**: {fuente}")
            col3.markdown(f"**Timestamp**: `{ts}`")

    if len(alertas_filtradas) > 20:
        st.caption(f"Mostrando las primeras 20 de {len(alertas_filtradas)}. Ver todas en **Historial**.")


def pagina_overview(alertas: list, df: pd.DataFrame):
    st.markdown("""
    <div class="radar-header">
        <p class="radar-title">🛡️ RADAR by ARGOS</p>
        <p class="radar-subtitle">Monitoreo de Crisis Reputacionales · Banco Promerica Costa Rica · RADAR detecta. ARGOS interpreta. Vos decidís.</p>
    </div>
    """, unsafe_allow_html=True)

    # Semáforo
    estado, desc = calcular_semaforo(alertas, ventana_horas=6)
    col_sem, col_metrics = st.columns([1, 3])

    with col_sem:
        render_semaforo(estado, desc)

    with col_metrics:
        total     = len(alertas)
        criticas  = sum(1 for a in alertas if a.get("criticidad") == "CRÍTICA")
        altas     = sum(1 for a in alertas if a.get("criticidad") == "ALTA")
        ultima_ts = alertas[0].get("timestamp", "—")[:16].replace("T", " ") if alertas else "—"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total alertas", total)
        c2.metric("🚨 Críticas", criticas)
        c3.metric("🔴 Altas", altas)
        c4.metric("Última detección", ultima_ts)

    st.divider()

    if df.empty:
        st.info("No hay alertas disponibles. Verificá la configuración en la sección **Config**.")
        return

    # ── Gráfico 1: Alertas por día ──────────────────────────────────────────
    st.subheader("📅 Alertas detectadas por día")
    st.caption("Cada barra muestra cuántas alertas generó el monitor en ese día, agrupadas por nivel de urgencia. "
               "Hacé clic en una barra para ver qué detectó el sistema ese día.")

    if "fecha" in df.columns:
        df_dia = df.groupby(["fecha", "criticidad"]).size().reset_index(name="count")
        fig_bar = px.bar(
            df_dia, x="fecha", y="count", color="criticidad",
            color_discrete_map=COLOR_CRITICIDAD,
            labels={"fecha": "Fecha", "count": "Cantidad de alertas", "criticidad": "Nivel de urgencia"},
            template="plotly_dark",
            text="count",
        )
        fig_bar.update_traces(textposition="inside", textfont_size=13)
        fig_bar.update_layout(
            plot_bgcolor="#0D0D14", paper_bgcolor="#0D0D14",
            legend_title_text="Nivel de urgencia",
            height=320,
            xaxis_title="",
            yaxis_title="Alertas",
            bargap=0.3,
        )
        sel_bar = st.plotly_chart(fig_bar, use_container_width=True, on_select="rerun", key="chart_bar")

        # Reaccionar al clic en la barra
        if sel_bar and sel_bar.get("selection", {}).get("points"):
            punto = sel_bar["selection"]["points"][0]
            fecha_sel = punto.get("x", "")
            crit_sel  = punto.get("legendgroup") or punto.get("curveNumber", "")
            if fecha_sel:
                st.markdown(f"---")
                filtradas = [
                    a for a in alertas
                    if str(a.get("timestamp", "")).startswith(fecha_sel)
                ]
                _render_alertas_filtradas(filtradas, f"Alertas del {fecha_sel}")

    st.divider()

    # ── Gráfico 2: Distribución por fuente ──────────────────────────────────
    st.subheader("📡 ¿De dónde vienen las alertas?")
    st.caption("Muestra qué canales de monitoreo están generando más señales. "
               "Hacé clic en un segmento para ver exactamente qué encontró el sistema en esa fuente.")

    df_fuente = df["source_type"].value_counts().reset_index()
    df_fuente.columns = ["source_type", "count"]
    df_fuente["fuente"] = df_fuente["source_type"].map(FUENTE_LABELS).fillna(df_fuente["source_type"])

    col_pie, col_desc = st.columns([2, 1])

    with col_pie:
        fig_pie = px.pie(
            df_fuente, values="count", names="fuente",
            color="fuente",
            color_discrete_map=FUENTE_COLORES,
            template="plotly_dark",
            hole=0.45,
        )
        fig_pie.update_traces(
            textposition="outside",
            textinfo="label+percent",
            textfont_size=12,
            pull=[0.05] * len(df_fuente),
        )
        fig_pie.update_layout(
            plot_bgcolor="#0D0D14", paper_bgcolor="#0D0D14",
            showlegend=False,
            height=380,
            margin=dict(t=20, b=20, l=20, r=20),
        )
        sel_pie = st.plotly_chart(fig_pie, use_container_width=True, on_select="rerun", key="chart_pie")

    with col_desc:
        st.markdown("**¿Qué monitorea cada fuente?**")
        for src, label in FUENTE_LABELS.items():
            if src in df["source_type"].values:
                count = int(df[df["source_type"] == src].shape[0])
                desc  = FUENTE_DESCRIPCIONES.get(src, "")
                st.markdown(f"**{label}** — {count} alertas")
                st.caption(desc)
                st.markdown("")

    # Reaccionar al clic en el pie
    if sel_pie and sel_pie.get("selection", {}).get("points"):
        punto      = sel_pie["selection"]["points"][0]
        fuente_sel = punto.get("label", "")
        # Invertir el label para obtener el source_type
        src_sel = next((k for k, v in FUENTE_LABELS.items() if v == fuente_sel), None)
        if src_sel:
            st.markdown("---")
            filtradas = [a for a in alertas if a.get("source_type") == src_sel]
            _render_alertas_filtradas(
                filtradas,
                f"{fuente_sel} — {FUENTE_DESCRIPCIONES.get(src_sel, '')}"
            )


def pagina_historial(alertas: list, df: pd.DataFrame):
    st.header("📋 Historial de Alertas")

    if df.empty:
        st.info("No hay alertas disponibles.")
        return

    # Filtros
    st.sidebar.subheader("Filtros")

    # Botón reset — limpia filtros volviendo a estado completo
    if st.sidebar.button("🔄 Ver todas las alertas", use_container_width=True):
        st.session_state.pop("hist_criticidad", None)
        st.session_state.pop("hist_fuente", None)
        st.rerun()

    criticidades_disp = sorted(df["criticidad"].unique().tolist())
    sel_criticidad = st.sidebar.multiselect(
        "Criticidad", criticidades_disp,
        default=st.session_state.get("hist_criticidad", criticidades_disp),
        key="hist_criticidad",
    )
    fuentes_disp = sorted(df["source_type"].unique().tolist())
    sel_fuente = st.sidebar.multiselect(
        "Fuente", fuentes_disp,
        default=st.session_state.get("hist_fuente", fuentes_disp),
        key="hist_fuente",
    )

    # Filtro de fechas
    if "ts_dt" in df.columns and df["ts_dt"].notna().any():
        fecha_min = df["ts_dt"].min().date()
        fecha_max = df["ts_dt"].max().date()
        rango = st.sidebar.date_input(
            "Rango de fechas",
            value=(fecha_min, fecha_max),
            min_value=fecha_min,
            max_value=fecha_max,
        )
    else:
        rango = None

    # Aplicar filtros
    mask = df["criticidad"].isin(sel_criticidad) & df["source_type"].isin(sel_fuente)
    if rango and len(rango) == 2:
        mask &= (df["ts_dt"].dt.date >= rango[0]) & (df["ts_dt"].dt.date <= rango[1])
    df_filtrado = df[mask]

    ocultas = len(df) - len(df_filtrado)
    if ocultas > 0:
        st.warning(
            f"⚠️ Filtros activos — mostrando **{len(df_filtrado)} de {len(df)}** alertas. "
            f"**{ocultas} alertas ocultas por el filtro.** "
            f"Hacé clic en **'🔄 Ver todas las alertas'** en el sidebar para resetear.",
            icon=None,
        )
    else:
        st.caption(f"Mostrando {len(df_filtrado)} de {len(df)} alertas")

    # Mostrar alertas como expanders
    for _, row in df_filtrado.iterrows():
        crit  = row.get("criticidad", "NORMAL")
        emoji = EMOJIS_CRITICIDAD.get(crit, "•")
        ts    = str(row.get("timestamp", ""))[:16].replace("T", " ")
        msg   = str(row.get("mensaje", ""))[:80]
        fuente = str(row.get("source_type", ""))
        label = f"{emoji} `{ts}` — **{crit}** · {fuente} · {msg}"

        with st.expander(label, expanded=False):
            # Titular / texto del tweet si existe
            original = next(
                (a for a in alertas if a.get("timestamp", "") == row.get("timestamp", "")),
                None
            )
            titular_orig = ""
            if original:
                titular_orig = (original.get("titular") or original.get("titulo")
                                or original.get("texto", ""))[:200]
            if titular_orig:
                st.markdown(f"*{titular_orig}*")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Criticidad**: `{crit}`")
                st.markdown(f"**Fuente**: `{fuente}`")
                st.markdown(f"**Cluster**: `{row.get('cluster', '—')}`")
            with col2:
                url = _get_url(original) if original else str(row.get("url") or "").strip()
                if url and url.startswith("http"):
                    st.link_button("🌐 Abrir fuente", url)
                    st.caption(f"[{url[:55]}...]({url})" if len(url) > 55 else f"[{url}]({url})")
                if original and original.get("usuario"):
                    seg = original.get("seguidores", 0)
                    st.markdown(f"**Twitter**: {original['usuario']}"
                                + (f" · {seg:,} seg" if seg else "")
                                + f" · 🔁{original.get('retweets',0)} ❤️{original.get('likes',0)}")
            st.markdown(f"**Mensaje**: {row.get('mensaje', '')}")

            # Buscar alert original para mostrar JSON
            original = next(
                (a for a in alertas if a.get("timestamp", "") == row.get("timestamp", "")),
                None
            )
            if original:
                with st.expander("Ver JSON completo", expanded=False):
                    st.json(original)


def pagina_detalle(alertas: list):
    st.header("🔍 Detalle de Alerta")

    if not alertas:
        st.info("No hay alertas disponibles.")
        return

    opciones = {
        f"{a.get('timestamp','')[:16]} | {a.get('criticidad','?')} | {str(a.get('mensaje',''))[:60]}": i
        for i, a in enumerate(alertas)
    }
    seleccion = st.selectbox("Seleccioná una alerta:", list(opciones.keys()))
    idx = opciones[seleccion]
    alerta = alertas[idx]

    crit   = alerta.get("criticidad", "NORMAL")
    color  = COLORES.get(crit, "#555")
    emoji  = EMOJIS_CRITICIDAD.get(crit, "•")

    st.markdown(f"### {emoji} {crit}")
    st.markdown(f"**{alerta.get('mensaje', '')}**")
    st.divider()

    hit = alerta.get("hit") or {}

    col1, col2, col3 = st.columns(3)
    col1.metric("Criticidad", crit)
    col2.metric("Score", f"{alerta.get('score', 0):.1f}")
    col3.metric("Fuente", alerta.get("source_type", "—"))

    st.markdown(f"**Timestamp**: `{alerta.get('timestamp', '—')}`")
    st.markdown(f"**Cluster**: `{alerta.get('cluster', '—')}`")
    st.markdown(f"**Caso ID**: `{alerta.get('caso_id', '—')}`")

    url = _get_url(alerta)
    if url and url.startswith("http"):
        st.link_button("🌐 Abrir fuente", url, use_container_width=True)
        st.caption(f"[{url[:70]}...]({url})" if len(url) > 70 else f"[{url}]({url})")

    if hit.get("title"):
        st.markdown(f"**Título**: {hit['title']}")

    if hit.get("text"):
        st.markdown("**Extracto del contenido:**")
        st.text_area("", hit["text"][:1000], height=150, disabled=True)

    if hit.get("usuario"):
        st.markdown(f"**Twitter**: @{hit['usuario']} · 🔁{hit.get('retweets',0)} ❤️{hit.get('likes',0)}")

    # Campos especiales SUGEF
    entidad = hit.get("entidad") or alerta.get("entidad", "")
    monto   = hit.get("monto_colones") or alerta.get("monto_colones", "")
    if entidad or monto:
        st.divider()
        st.subheader("📋 Datos SUGEF")
        if entidad:
            st.markdown(f"**Entidad sancionada**: {entidad}")
        if monto:
            st.markdown(f"**Monto de sanción**: {monto}")

    st.divider()
    st.subheader("JSON completo")
    st.json(alerta)


def pagina_config():
    st.header("⚙️ Configuración")

    # Estado del Gist
    st.subheader("Fuentes de datos")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**GitHub Gist (Railway)**")
        gist_id  = os.getenv("GITHUB_GIST_ID", "")
        gist_tok = os.getenv("GITHUB_GIST_TOKEN", "")
        if gist_id and gist_tok:
            try:
                from gist_sync import estado_gist
                info = estado_gist()
                if info.get("configurado"):
                    st.success(f"✅ Conectado — {info.get('total', '?')} alertas")
                    st.caption(f"Última actualización: {info.get('updated_at', '—')}")
                    st.caption(f"Gist ID: {info.get('gist_id', '—')}")
                else:
                    st.error(f"❌ Error: {info.get('error', 'desconocido')}")
            except Exception as e:
                st.error(f"❌ gist_sync no disponible: {e}")
        else:
            st.warning("⚠️ No configurado")
            st.code("GITHUB_GIST_ID=...\nGITHUB_GIST_TOKEN=...", language="bash")

    with col2:
        st.markdown("**Alertas locales**")
        alerts_dir = _directorio_alertas_local()
        archivos   = glob.glob(f"{alerts_dir}/*.json") if os.path.isdir(alerts_dir) else []
        if archivos:
            st.success(f"✅ {len(archivos)} archivos en `{alerts_dir}`")
        else:
            st.warning(f"⚠️ Sin archivos locales en `{alerts_dir}`")
            st.caption("Podés configurar la ruta con `RADAR_ALERTS_DIR`")

    st.divider()

    st.subheader("Cómo configurar")
    st.markdown("""
    **Variables de entorno requeridas (agregar a `.env` o exportar en terminal):**
    """)
    st.code("""GITHUB_GIST_ID=<ID del Gist — visible en la URL>
GITHUB_GIST_TOKEN=<ghp_... — Personal Access Token con permiso gist>
RADAR_ALERTS_DIR=<ruta a la carpeta alerts/ local>""", language="bash")

    st.markdown("""
    **Arrancar el dashboard:**
    ```bash
    streamlit run radar_dashboard.py
    ```
    """)

    st.divider()
    st.subheader("Acciones")
    if st.button("🔄 Forzar actualización de datos"):
        cargar_alertas.clear()
        st.success("Cache limpiado. Recargando...")
        st.rerun()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    st.sidebar.markdown("## 🛡️ RADAR by ARGOS")
    st.sidebar.markdown("---")

    pagina = st.sidebar.radio(
        "Navegación",
        ["📊 Overview", "📋 Historial", "🔍 Detalle", "⚙️ Config"],
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("RADAR detecta. ARGOS interpreta. Vos decidís.")

    alertas = cargar_alertas()
    df = alertas_a_df(alertas)

    if pagina == "📊 Overview":
        pagina_overview(alertas, df)
    elif pagina == "📋 Historial":
        pagina_historial(alertas, df)
    elif pagina == "🔍 Detalle":
        pagina_detalle(alertas)
    elif pagina == "⚙️ Config":
        pagina_config()


if __name__ == "__main__":
    main()
