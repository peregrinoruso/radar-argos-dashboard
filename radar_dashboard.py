#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar_dashboard.py — RADAR by ARGOS
Dashboard Streamlit de monitoreo de crisis reputacionales.

Uso:
    streamlit run radar_dashboard.py

Variables de entorno (configurar en Streamlit Cloud → Secrets):
    GITHUB_GIST_ID      — ID del Gist con historial de alertas
    GITHUB_GIST_TOKEN   — Token GitHub (lectura)
"""

import sys
import os

# Asegurar que gist_sync.py sea importable desde cualquier directorio de ejecución
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
    .semaforo-rojo    { background: #2d0a0a; border: 2px solid #C0392B; }
    .semaforo-naranja { background: #2d1a0a; border: 2px solid #F06A1A; }
    .semaforo-verde   { background: #0a2d0a; border: 2px solid #27AE60; }
    .semaforo-gris    { background: #1a1a1a; border: 2px solid #555; }
    .semaforo-icon  { font-size: 3rem; }
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
    script_dir = Path(__file__).parent
    return str(script_dir / "alerts")


@st.cache_data(ttl=120)
def cargar_alertas() -> list[dict]:
    """Carga alertas del Gist y/o carpeta local. Dedup por timestamp."""
    alertas = []

    # 1. Intentar Gist
    try:
        from gist_sync import cargar_alertas_gist
        gist_alerts = cargar_alertas_gist()
        if gist_alerts:
            alertas.extend(gist_alerts)
    except Exception:
        pass

    # 2. Fallback: archivos locales
    alerts_dir = _directorio_alertas_local()
    if os.path.isdir(alerts_dir):
        archivos = sorted(glob.glob(f"{alerts_dir}/*.json"))
        ts_en_gist = {a.get("timestamp") for a in alertas}
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
            except Exception:
                pass

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
            "url":         hit.get("url") or a.get("url", ""),
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
    "keywords_exa":    "🔍 Búsqueda web profunda",
    "gaceta":          "📋 Gaceta Oficial",
    "conassif":        "🏦 CONASSIF",
    "otros":           "⚪ Sin clasificar",
}

FUENTE_DESCRIPCIONES = {
    "sugef_sanciones": "Cambios en la página oficial de sanciones de SUGEF. Alta relevancia regulatoria.",
    "sugef_doc":       "Circulares, resoluciones y documentos nuevos publicados por SUGEF.",
    "medios_cr":       "Artículos en medios costarricenses que mencionan keywords críticas del caso.",
    "twitter":         "Tweets con menciones relevantes detectados vía Apify.",
    "keywords_exa":    "Resultados de búsqueda profunda en la web con keywords configuradas.",
    "gaceta":          "Publicaciones nuevas en La Gaceta Oficial de Costa Rica.",
    "conassif":        "Documentos o cambios detectados en el sitio del CONASSIF.",
    "otros":           "Alertas sin fuente identificada (generadas en las primeras corridas del monitor).",
}

FUENTE_COLORES = {
    "🏛️ SUGEF Sanciones":        "#C0392B",
    "📄 SUGEF Documentos":        "#E8A020",
    "📰 Medios de comunicación":  "#2980B9",
    "🐦 Twitter / X":             "#1DA1F2",
    "🔍 Búsqueda web profunda":   "#27AE60",
    "📋 Gaceta Oficial":          "#8E44AD",
    "🏦 CONASSIF":                "#16A085",
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
    hit = a.get("hit") or {}
    url = a.get("url") or hit.get("url") or ""
    if url in ("N/A", "n/a", "—", "-", ""):
        return ""
    return url


def _render_alertas_filtradas(alertas_filtradas: list, titulo: str):
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

        link_icon = " 🔗" if url else ""
        with st.expander(f"{emoji} `{ts}` — **{crit}** · {msg[:65]}{link_icon}", expanded=False):

            if url:
                st.link_button("🌐 Abrir fuente original", url, use_container_width=True)
            else:
                st.caption("⚠️ Esta alerta no tiene URL registrada.")

            st.divider()

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

            titular = a.get("titular", "")
            medio   = a.get("medio", "")
            kw      = a.get("keywords_match", "")
            if titular or medio:
                st.markdown("##### 📰 Artículo detectado")
                if titular: st.markdown(f"**Titular**: *{titular}*")
                if medio:   st.markdown(f"**Medio**: `{medio.upper()}`")
                if kw:      st.markdown(f"**Keyword que lo activó**: `{kw}`")
                st.divider()

            sha = a.get("sha256", "")
            fuente_doc = a.get("fuente", "")
            if sha:
                st.markdown("##### 📄 Documento regulatorio")
                if fuente_doc: st.markdown(f"**Publicado por**: {fuente_doc}")
                st.caption(f"SHA-256: `{sha[:16]}…`")
                st.divider()

            hit = a.get("hit") or {}
            if hit.get("usuario"):
                st.markdown("##### 🐦 Tweet detectado")
                st.markdown(f"**@{hit['usuario']}** — RT:{hit.get('retweets',0)} ❤️{hit.get('likes',0)}")
                if hit.get("text"):
                    st.text(hit["text"][:300])
                st.divider()

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

    st.subheader("📅 Alertas detectadas por día")
    st.caption("Cada barra muestra cuántas alertas generó el monitor en ese día, agrupadas por nivel de urgencia.")

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

        if sel_bar and sel_bar.get("selection", {}).get("points"):
            punto = sel_bar["selection"]["points"][0]
            fecha_sel = punto.get("x", "")
            if fecha_sel:
                st.markdown("---")
                filtradas = [
                    a for a in alertas
                    if str(a.get("timestamp", "")).startswith(fecha_sel)
                ]
                _render_alertas_filtradas(filtradas, f"Alertas del {fecha_sel}")

    st.divider()

    st.subheader("📡 ¿De dónde vienen las alertas?")
    st.caption("Muestra qué canales de monitoreo están generando más señales.")

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

    if sel_pie and sel_pie.get("selection", {}).get("points"):
        punto      = sel_pie["selection"]["points"][0]
        fuente_sel = punto.get("label", "")
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

    st.sidebar.subheader("Filtros")
    criticidades_disp = sorted(df["criticidad"].unique().tolist())
    sel_criticidad = st.sidebar.multiselect(
        "Criticidad", criticidades_disp, default=criticidades_disp
    )
    fuentes_disp = sorted(df["source_type"].unique().tolist())
    sel_fuente = st.sidebar.multiselect(
        "Fuente", fuentes_disp, default=fuentes_disp
    )

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

    mask = df["criticidad"].isin(sel_criticidad) & df["source_type"].isin(sel_fuente)
    if rango and len(rango) == 2:
        mask &= (df["ts_dt"].dt.date >= rango[0]) & (df["ts_dt"].dt.date <= rango[1])
    df_filtrado = df[mask]

    st.caption(f"Mostrando {len(df_filtrado)} de {len(df)} alertas")

    for _, row in df_filtrado.iterrows():
        crit  = row.get("criticidad", "NORMAL")
        emoji = EMOJIS_CRITICIDAD.get(crit, "•")
        ts    = str(row.get("timestamp", ""))[:16].replace("T", " ")
        msg   = str(row.get("mensaje", ""))[:80]
        fuente = str(row.get("source_type", ""))
        label = f"{emoji} `{ts}` — **{crit}** · {fuente} · {msg}"

        with st.expander(label, expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Criticidad**: `{crit}`")
                st.markdown(f"**Fuente**: `{fuente}`")
                st.markdown(f"**Cluster**: `{row.get('cluster', '—')}`")
                st.markdown(f"**Score**: `{row.get('score', 0):.1f}`")
            with col2:
                url = row.get("url", "")
                if url:
                    st.markdown(f"**URL**: [{url[:60]}...]({url})")
                if row.get("usuario"):
                    st.markdown(f"**Usuario**: @{row['usuario']} · RT:{row.get('retweets',0)} ❤️{row.get('likes',0)}")
            st.markdown(f"**Mensaje completo**: {row.get('mensaje', '')}")

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

    url = hit.get("url") or alerta.get("url", "")
    if url:
        st.markdown(f"**URL**: [{url}]({url})")

    if hit.get("title"):
        st.markdown(f"**Título**: {hit['title']}")

    if hit.get("text"):
        st.markdown("**Extracto del contenido:**")
        st.text_area("", hit["text"][:1000], height=150, disabled=True)

    if hit.get("usuario"):
        st.markdown(f"**Twitter**: @{hit['usuario']} · 🔁{hit.get('retweets',0)} ❤️{hit.get('likes',0)}")

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
            st.warning(f"⚠️ Sin archivos locales")
            st.caption("Solo el Gist está activo como fuente de datos.")

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
