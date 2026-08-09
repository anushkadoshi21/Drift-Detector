"""
Drift Detector — dashboard.

Landing (problems first):
  analysis cards -> "Drifting now": one card per drifting RESOURCE (aspects
  grouped inside, Option A), expand to see each aspect as its OWN card with a
  full timeline. Each timeline item lists every attribute as
  `attribute: declared -> actual`; add/remove drift (one side None) renders as
  `added:` / `removed:` instead of a broken arrow.
  -> "Browse all resources": type cards -> drill into a type.

Run:  pip install streamlit boto3 streamlit-autorefresh ; streamlit run app.py
"""

import os
from datetime import datetime, timezone, timedelta

import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
    HAVE_AUTOREFRESH = True
except ImportError:
    HAVE_AUTOREFRESH = False

TABLE_NAME      = os.getenv("TABLE_NAME", "drift-detector-history")
AWS_REGION      = os.getenv("AWS_REGION", "us-east-1")
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "20"))

INK, MUTED, SURFACE, CARD, HAIRLINE = "#1A1D21", "#6B7280", "#F7F8FA", "#FFFFFF", "#E3E6EA"
FIRING, FIRING_BG     = "#C4443A", "#FBEBEA"
RESOLVED, RESOLVED_BG = "#3F7D5B", "#EAF2ED"
ASPECT_BG = "#FCFCFD"

st.set_page_config(page_title="Drift Detector", page_icon="◆", layout="wide")
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
html, body, [class*="css"] {{ font-family:'IBM Plex Sans',sans-serif; }}
.stApp {{ background:{SURFACE}; }}
.block-container {{ padding-top:2rem; max-width:1080px; }}
.dd-title {{ font-size:1.5rem; font-weight:700; color:{INK}; letter-spacing:-.01em; margin:0; }}
.dd-sub {{ font-family:'IBM Plex Mono',monospace; font-size:.76rem; color:{MUTED}; margin:.15rem 0 .3rem; }}
.dd-metric {{ background:{CARD}; border:1px solid {HAIRLINE}; border-radius:11px; padding:.9rem 1rem; }}
.dd-metric .v {{ font-family:'IBM Plex Mono',monospace; font-size:1.7rem; font-weight:600; color:{INK}; line-height:1; }}
.dd-metric .v.fire {{ color:{FIRING}; }}
.dd-metric .l {{ font-family:'IBM Plex Mono',monospace; font-size:.68rem; letter-spacing:.06em;
                text-transform:uppercase; color:{MUTED}; margin-top:.4rem; }}
.dd-section {{ font-family:'IBM Plex Mono',monospace; font-size:.72rem; font-weight:600; letter-spacing:.08em;
             text-transform:uppercase; color:{MUTED}; margin:1.7rem 0 .6rem;
             padding-bottom:.3rem; border-bottom:1px solid {HAIRLINE}; }}
.dd-section .hot {{ color:{FIRING}; }}
.dd-crumb {{ font-family:'IBM Plex Mono',monospace; font-size:.76rem; color:{MUTED}; margin:1rem 0 .5rem; }}
.dd-crumb b {{ color:{INK}; }}
.dd-card {{ background:{CARD}; border:1px solid {HAIRLINE}; border-radius:11px; padding:.8rem 1rem .3rem; margin-bottom:.2rem; }}
.dd-card.hot {{ border-left:4px solid {FIRING}; }}
.dd-card.ok  {{ border-left:4px solid {RESOLVED}; }}
.dd-cardname {{ font-family:'IBM Plex Mono',monospace; font-weight:600; font-size:.95rem; color:{INK}; }}
.dd-cardtype {{ font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:{MUTED}; margin-left:.5rem; }}
.dd-cardmeta {{ font-family:'IBM Plex Mono',monospace; font-size:.73rem; color:{MUTED}; margin-top:.3rem; }}
.dd-cardmeta .fire {{ color:{FIRING}; }} .dd-cardmeta .ok {{ color:{RESOLVED}; }}

/* aspect = its own card */
.dd-aspect {{ background:{ASPECT_BG}; border:1px solid {HAIRLINE}; border-radius:9px;
             padding:.7rem .85rem .75rem; margin:.55rem 0; }}
.dd-aspect.hot {{ border-left:3px solid {FIRING}; }}
.dd-aspect.ok  {{ border-left:3px solid {RESOLVED}; }}
.dd-asphead {{ display:flex; align-items:center; justify-content:space-between; }}
.dd-aspname {{ font-family:'IBM Plex Mono',monospace; font-size:.86rem; font-weight:600; color:{INK}; }}
.dd-badge {{ font-family:'IBM Plex Mono',monospace; font-size:.65rem; font-weight:600; padding:.12rem .5rem; border-radius:5px; }}
.dd-badge.firing   {{ color:{FIRING};   background:{FIRING_BG}; }}
.dd-badge.resolved {{ color:{RESOLVED}; background:{RESOLVED_BG}; }}
.dd-meta {{ font-family:'IBM Plex Mono',monospace; font-size:.71rem; color:{MUTED}; margin:.35rem 0 .1rem; }}

.dd-tlhdr {{ font-family:'IBM Plex Mono',monospace; font-size:.66rem; font-weight:600; letter-spacing:.07em;
           text-transform:uppercase; color:{MUTED}; margin:.6rem 0 .3rem; }}
.dd-tl {{ border-left:2px solid {HAIRLINE}; margin:.2rem 0 .1rem .3rem; padding-left:.9rem; }}
.dd-ev {{ position:relative; padding:.15rem 0 .7rem; }}
.dd-ev::before {{ content:''; position:absolute; left:-1.0rem; top:.35rem; width:8px; height:8px;
                 border-radius:50%; background:{FIRING}; border:2px solid {ASPECT_BG}; }}
.dd-ev.res::before {{ background:{RESOLVED}; }}
.dd-ev-when {{ font-family:'IBM Plex Mono',monospace; font-size:.71rem; color:{MUTED}; }}
.dd-ev-attr {{ font-family:'IBM Plex Mono',monospace; font-size:.78rem; color:{INK}; margin-top:.15rem; }}
.dd-attrname {{ color:{INK}; font-weight:600; }}
.dd-declared {{ color:{RESOLVED}; }} .dd-actual {{ color:{FIRING}; }}
.dd-arrow {{ color:{MUTED}; }}
.dd-tag {{ font-family:'IBM Plex Mono',monospace; font-size:.64rem; font-weight:600; padding:.05rem .35rem;
          border-radius:4px; margin-right:.4rem; }}
.dd-tag.add {{ color:{FIRING}; background:{FIRING_BG}; }}
.dd-tag.rem {{ color:{MUTED}; background:{HAIRLINE}; }}
.dd-ev-res {{ font-family:'IBM Plex Sans',sans-serif; font-size:.8rem; color:{RESOLVED}; margin-top:.15rem; }}

.dd-empty {{ font-family:'IBM Plex Mono',monospace; font-size:.82rem; color:{MUTED};
            background:{CARD}; border:1px dashed {HAIRLINE}; border-radius:10px; padding:1rem; text-align:center; }}
/* make the expander trigger prominent */
div[data-testid="stExpander"] details summary {{
    background:{FIRING_BG}; border:1px solid {FIRING}; border-radius:9px;
    padding:.55rem .85rem !important; font-family:'IBM Plex Mono',monospace;
    font-weight:600; font-size:.82rem; color:{FIRING}; }}
div[data-testid="stExpander"] details summary:hover {{ background:#F7DEDC; }}
div[data-testid="stExpander"] {{ border:none !important; box-shadow:none !important; }}
#MainMenu, footer {{ visibility:hidden; }}
</style>
""", unsafe_allow_html=True)



def load_records():
    import boto3
    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)
    resp = table.scan()
    items = resp.get("Items", [])
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def fmt_ts(raw):
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%b %d, %H:%M UTC")
    except (ValueError, TypeError):
        return str(raw)


def is_firing(aspects): return any(a.get("status") == "FIRING" for a in aspects)
def n_firing(aspects):  return sum(1 for a in aspects if a.get("status") == "FIRING")


def attr_line(a):
    """Render one attribute triple. None on one side -> added:/removed: tag,
    otherwise declared -> actual."""
    name = a.get("attribute", "?")
    dec, act = a.get("declared"), a.get("actual")
    if dec is None and act is not None:          # added
        return f'<div class="dd-ev-attr"><span class="dd-tag add">ADDED</span>' \
               f'<span class="dd-attrname">{name}</span>: <span class="dd-actual">{act}</span></div>'
    if act is None and dec is not None:          # removed
        return f'<div class="dd-ev-attr"><span class="dd-tag rem">REMOVED</span>' \
               f'<span class="dd-attrname">{name}</span>: <span class="dd-declared">{dec}</span></div>'
    return f'<div class="dd-ev-attr"><span class="dd-attrname">{name}</span>: ' \
           f'<span class="dd-declared">{dec}</span> <span class="dd-arrow">→</span> ' \
           f'<span class="dd-actual">{act}</span></div>'


def render_aspect_card(rec):
    status = rec.get("status")
    history = rec.get("history", [])
    firing = status == "FIRING"
    in_sync = status == "RESOLVED" and not history      # baseline: never drifted

    if firing:
        cls, bcls, label = "hot", "firing", "DRIFTING"
    elif in_sync:
        cls, bcls, label = "ok", "resolved", "IN SYNC"
    else:
        cls, bcls, label = "ok", "resolved", "RESOLVED"

    st.markdown(f'<div class="dd-aspect {cls}">'
                f'<div class="dd-asphead"><span class="dd-aspname">{rec.get("aspect","—")}</span>'
                f'<span class="dd-badge {bcls}">{label}</span></div>', unsafe_allow_html=True)

    if firing:
        meta = f"first seen {fmt_ts(rec.get('first_seen'))} · last checked {fmt_ts(rec.get('last_seen'))} · alerts {rec.get('notification_count','—')}"
    elif in_sync:
        meta = f"in sync since {fmt_ts(rec.get('first_seen'))} · last checked {fmt_ts(rec.get('last_seen'))}"
    else:
        meta = f"resolved {fmt_ts(rec.get('resolved_at'))} · first seen {fmt_ts(rec.get('first_seen'))}"
    st.markdown(f'<div class="dd-meta">{meta}</div>', unsafe_allow_html=True)

    if history:
        st.markdown('<div class="dd-tlhdr">Timeline</div>', unsafe_allow_html=True)
        rows = ['<div class="dd-tl">']
        for ep in history:
            rows.append(f'<div class="dd-ev"><div class="dd-ev-when">detected {fmt_ts(ep.get("detected_at"))}'
                        + (f' · last seen {fmt_ts(ep.get("last_seen"))}' if ep.get("last_seen") else "")
                        + '</div>')
            for a in ep.get("attributes", []):
                rows.append(attr_line(a))
            if ep.get("resolved_at"):
                rows.append(f'<div class="dd-ev-res">✓ resolved {fmt_ts(ep.get("resolved_at"))} — back in sync</div>')
            rows.append('</div>')
        rows.append('</div>')
        st.markdown("".join(rows), unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
def render_resource_card(rtype, rname, aspects, key, show_type=False):
    hot = is_firing(aspects); nf = n_firing(aspects)
    tspan = f'<span class="dd-cardtype">{rtype}</span>' if show_type else ""
    meta = (f'<span class="fire">{nf} drifting aspect{"s" if nf!=1 else ""}</span>' if nf
            else '<span class="ok">in sync</span>') + f' · {len(aspects)} total'
    st.markdown(f'<div class="dd-card {"hot" if hot else "ok"}">'
                f'<div class="dd-cardname">{rname}{tspan}</div>'
                f'<div class="dd-cardmeta">{meta}</div></div>', unsafe_allow_html=True)
    with st.expander(f"▸  View aspects & timelines  ({len(aspects)})", expanded=False):
        for a in sorted(aspects, key=lambda r: r.get("status") != "FIRING"):
            render_aspect_card(a)


# ---- page -----------------------------------------------------------------
if HAVE_AUTOREFRESH :
    st_autorefresh(interval=REFRESH_SECONDS * 1000, key="poll")

st.session_state.setdefault("sel_type", None)
def go_type(t): st.session_state.sel_type = t
def go_home():  st.session_state.sel_type = None

now = datetime.now(timezone.utc).strftime("%b %d, %H:%M:%S UTC")
st.markdown(f'<div class="dd-title">Drift Detector</div>'
            f'<div class="dd-sub">terraform state vs. live AWS · {now}'
            + '</div>', unsafe_allow_html=True)

try:
    records = load_records()
except Exception as e:
    st.markdown(f'<div class="dd-empty">Could not read the drift table.<br>'
                f'region {AWS_REGION} · table {TABLE_NAME}<br>'
                f'<span style="color:{FIRING}">{type(e).__name__}: {e}</span></div>', unsafe_allow_html=True)
    st.stop()
if not records:
    st.markdown('<div class="dd-empty">No monitored resources yet.</div>', unsafe_allow_html=True)
    st.stop()

tree = {}
for r in records:
    rtype = r.get("resource_type", "Uncategorized")
    rname = r.get("resource_name", r.get("resource_id", "unknown"))
    tree.setdefault(rtype, {}).setdefault(rname, []).append(r)

n_types = len(tree); n_resources = sum(len(v) for v in tree.values())
n_aspects = len(records); n_drift = n_firing(records)
c1, c2, c3, c4 = st.columns(4)
for col, val, lab, fire in [(c1, n_types, "Resource types", False), (c2, n_resources, "Resources", False),
                            (c3, n_aspects, "Aspects", False), (c4, n_drift, "Drifting now", n_drift > 0)]:
    col.markdown(f'<div class="dd-metric"><div class="v {"fire" if fire else ""}">{val}</div>'
                 f'<div class="l">{lab}</div></div>', unsafe_allow_html=True)

if st.session_state.sel_type is None or st.session_state.sel_type not in tree:
    drifting = [(rt, rn, asp) for rt, res in tree.items() for rn, asp in res.items() if is_firing(asp)]
    hot_lbl = f'<span class="hot">Drifting now — {len(drifting)}</span>' if drifting else "Drifting now — 0"
    st.markdown(f'<div class="dd-section">{hot_lbl}</div>', unsafe_allow_html=True)
    if not drifting:
        st.markdown('<div class="dd-empty">Nothing is drifting. All monitored resources match declared state.</div>', unsafe_allow_html=True)
    else:
        for rt, rn, asp in sorted(drifting, key=lambda x: (x[0], x[1])):
            render_resource_card(rt, rn, asp, key=f"drift_{rt}_{rn}", show_type=True)

    st.markdown('<div class="dd-section">Browse all resources</div>', unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (rtype, resources) in enumerate(sorted(tree.items(),
            key=lambda kv: not is_firing([a for asp in kv[1].values() for a in asp]))):
        flat = [a for asp in resources.values() for a in asp]; nf = n_firing(flat)
        with cols[i % 2]:
            st.markdown(f'<div class="dd-card {"hot" if nf else "ok"}">'
                        f'<div class="dd-cardname">{rtype}</div>'
                        f'<div class="dd-cardmeta">{len(resources)} resource{"s" if len(resources)!=1 else ""} · '
                        + (f'<span class="fire">{nf} drifting</span>' if nf else '<span class="ok">all in sync</span>')
                        + '</div></div>', unsafe_allow_html=True)
            st.button(f"Browse  {rtype}  →", key=f"t_{rtype}", use_container_width=True, on_click=go_type, args=(rtype,))
else:
    rtype = st.session_state.sel_type; resources = tree[rtype]
    st.button("←  Back to dashboard", key="back", on_click=go_home)
    st.markdown(f'<div class="dd-crumb"><b>{rtype}</b> · {len(resources)} resource{"s" if len(resources)!=1 else ""}</div>', unsafe_allow_html=True)
    for rname, aspects in sorted(resources.items(), key=lambda kv: not is_firing(kv[1])):
        render_resource_card(rtype, rname, aspects, key=f"br_{rtype}_{rname}", show_type=False)