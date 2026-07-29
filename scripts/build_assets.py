"""Build the README's animated SVGs, one dark and one light variant each.

Why a generator rather than six hand-written files: the light and dark variants
differ only by palette, and the numbers on them come from sample-data/summary.json.
Hand-maintaining six files guarantees they drift apart the first time the data
changes. Here one spec plus two palettes produces all six, and the figures are read
from the same computation that wrote the CSVs.

Animation notes, learned the hard way about GitHub:

  * GitHub strips <style> and <script> from markdown, so CSS cannot live in the
    README. It DOES run CSS inside an .svg referenced as <img>, which is why the
    animation lives in the asset rather than the page.
  * No external fonts can load - a proxied SVG has no network access - so every
    text element uses a generic system stack.
  * GitHub cannot be forced into dark mode. Both variants are emitted and the
    README picks between them with <picture> and prefers-color-scheme.
  * Entrance animations use `both` fill-mode so nothing sits invisible before its
    delay elapses, and they run once. Only the dataflow dashes loop.

Usage:  python scripts/build_assets.py
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SUMMARY = ROOT / "sample-data" / "summary.json"

FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Consolas,'Liberation Mono',monospace"

# The light palette is the report's own theme colours, so the README and the
# screenshots read as one piece of work. Dark is tuned to GitHub's dark surface.
LIGHT = {
    "name": "light",
    "bg": "#FFFFFF", "card": "#F6F8FA", "card2": "#EEF1F5", "border": "#D0D7DE",
    "text": "#1F2328", "muted": "#656D76", "faint": "#8C959F",
    "primary": "#2563EB", "good": "#059669", "warn": "#D97706", "bad": "#DC2626",
    "violet": "#7C3AED", "cyan": "#0891B2", "track": "#E4E8EC",
    "glow": "#2563EB", "glowOpacity": "0.07", "ctaShadow": "0.34",
    # Section-banner wash. Dark needs more of it: a 7% tint reads as nothing
    # against #0D1117, where the same value is clearly visible against white.
    "wash": "0.10",
}
DARK = {
    "name": "dark",
    "bg": "#0D1117", "card": "#161B22", "card2": "#1C2128", "border": "#30363D",
    "text": "#E6EDF3", "muted": "#8B949E", "faint": "#484F58",
    "primary": "#3B82F6", "good": "#10B981", "warn": "#F59E0B", "bad": "#EF4444",
    "violet": "#8B5CF6", "cyan": "#22D3EE", "track": "#21262D",
    # A coloured drop shadow that reads on white disappears on #0D1117, so dark
    # leans on a stronger one to keep the button lifted off the page.
    "glow": "#3B82F6", "glowOpacity": "0.16", "ctaShadow": "0.55",
    "wash": "0.22",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def paint(template: str, p: dict) -> str:
    """Substitute __TOKEN__ placeholders. Used instead of str.format so the CSS
    braces in the templates need no escaping."""
    out = template
    for k, v in p.items():
        out = out.replace(f"__{k.upper()}__", str(v))
    return out.replace("__FONT__", FONT).replace("__MONO__", MONO)


# --------------------------------------------------------------------------- #
# 1. Hero banner
# --------------------------------------------------------------------------- #

def hero(p: dict, s: dict) -> str:
    overstate = round((s["spend_double_counted"] - s["spend_total"]) / s["spend_total"] * 100)
    dbt_pct = round(s["spend_dbt"] / s["spend_total"] * 100, 1)

    # Two tiles for the warehouse-wide half, one for the dbt half, one for the
    # counting trap - the same balance the README is structured around. A banner
    # of four dbt figures would misdescribe a report that is half about
    # everything else running on the warehouse.
    kpis = [
        (f"${s['spend_total']:,.2f}", "billed across one month", "primary"),
        (f"{s['jobs_total']:,}", "BigQuery jobs, all types", "cyan"),
        (f"${s['spend_dbt']:,.2f}", f"{dbt_pct}% of the bill is dbt", "violet"),
        (f"+{overstate}%", "if script children were summed", "bad"),
    ]

    tiles = []
    x = 40
    for i, (value, label, colour) in enumerate(kpis):
        w = 268
        tiles.append(f"""
  <g class="tile" style="animation-delay:{0.35 + i * 0.11:.2f}s">
    <rect x="{x}" y="190" width="{w}" height="100" rx="10" fill="__CARD__" stroke="__BORDER__"/>
    <rect x="{x}" y="190" width="4" height="100" rx="2" fill="__{colour.upper()}__"/>
    <text x="{x + 22}" y="240" font-family="__FONT__" font-size="35" font-weight="700"
          fill="__TEXT__" letter-spacing="-0.5">{esc(value)}</text>
    <text x="{x + 22}" y="268" font-family="__FONT__" font-size="15.5"
          fill="__MUTED__">{esc(label)}</text>
  </g>""")
        x += w + 16

    badges = ["Power BI  ·  PBIP", "BigQuery", "dbt", "synthetic sample data"]
    bx = 40
    badge_svg = []
    for i, b in enumerate(badges):
        bw = 24 + len(b) * 8.4
        badge_svg.append(f"""
  <g class="badge" style="animation-delay:{0.85 + i * 0.07:.2f}s">
    <rect x="{bx:.0f}" y="312" width="{bw:.0f}" height="32" rx="16"
          fill="__CARD2__" stroke="__BORDER__"/>
    <text x="{bx + bw / 2:.0f}" y="333" text-anchor="middle" font-family="__FONT__"
          font-size="15.5" fill="__MUTED__">{esc(b)}</text>
  </g>""")
        bx += bw + 10

    template = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 366" width="1200" height="366" role="img" aria-label="BigQuery and dbt Cost Observability">
  <style>
    /* Entrance animations move things but never fade them. A renderer that does
       not run CSS - GitHub's mobile app, an email digest, a PDF export - would
       otherwise show an empty banner, because the from-state of a fade is
       invisible and fill-mode `both` holds it for the whole delay. Every element
       here is fully legible with the animation stripped out. */
    .tile {{ animation: rise .55s cubic-bezier(.2,.7,.3,1) both; }}
    .badge {{ animation: rise .45s ease-out both; }}
    .ttl {{ animation: rise .5s ease-out both; }}
    .rule {{ animation: sweep .9s cubic-bezier(.2,.7,.3,1) .15s; }}
    .sheen {{ animation: sheen 4.5s ease-in-out infinite 1.4s; }}
    .blob {{ animation: drift 14s ease-in-out infinite alternate; }}
    .blob2 {{ animation: drift2 18s ease-in-out infinite alternate; }}
    @keyframes rise {{ from {{ transform: translateY(9px) }}
                       to   {{ transform: translateY(0) }} }}
    @keyframes sweep {{ from {{ width: 0 }} to {{ width: 1120px }} }}
    @keyframes sheen {{ 0%,100% {{ transform: translateX(-160px); opacity: 0 }}
                        45% {{ opacity: .7 }}
                        90% {{ transform: translateX(1120px); opacity: 0 }} }}
    @keyframes drift {{ from {{ transform: translate(0,0) }} to {{ transform: translate(64px,-22px) }} }}
    @keyframes drift2 {{ from {{ transform: translate(0,0) }} to {{ transform: translate(-52px,18px) }} }}
    @media (prefers-reduced-motion: reduce) {{
      .tile,.badge,.ttl,.rule,.sheen,.blob,.blob2 {{ animation: none }}
    }}
  </style>
  <defs>
    <linearGradient id="sheenG" x1="0" x2="1">
      <stop offset="0%"   stop-color="__PRIMARY__" stop-opacity="0"/>
      <stop offset="50%"  stop-color="__PRIMARY__" stop-opacity="1"/>
      <stop offset="100%" stop-color="__PRIMARY__" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="ruleClip"><rect x="40" y="164" width="1120" height="3" rx="1.5"/></clipPath>
    <!-- Heavy blur turns the two circles into an ambient wash. Unblurred they
         read as hard discs sitting on top of the text. -->
    <filter id="soft" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="58"/>
    </filter>
  </defs>

  <rect width="1200" height="330" fill="__BG__"/>
  <g filter="url(#soft)" opacity="__GLOWOPACITY__">
    <circle class="blob"  cx="1060" cy="42"  r="118" fill="__GLOW__"/>
    <circle class="blob2" cx="96"   cy="312" r="92"  fill="__VIOLET__"/>
  </g>

  <g class="ttl">
    <text x="40" y="80" font-family="__FONT__" font-size="38" font-weight="700"
          fill="__TEXT__" letter-spacing="-0.8">BigQuery + dbt Cost Observability</text>
  </g>
  <!-- The strapline carries the whole point of the repo, so it is sized and
       coloured to be read, not to be decorative: __TEXT__ at 17px rather than a
       muted 14.5. The line under it is supporting detail and sits at __MUTED__ -
       __FAINT__ was too low-contrast to read in either palette, and worse on
       dark, where faint is darker than muted rather than lighter. -->
  <g class="ttl" style="animation-delay:.1s">
    <text x="40" y="116" font-family="__FONT__" font-size="20" fill="__TEXT__">
      Two halves: what the whole warehouse costs, and what dbt costs inside it.
    </text>
    <text x="40" y="146" font-family="__MONO__" font-size="15" fill="__MUTED__">
      Power BI semantic model  ·  runs on committed sample data, no cloud account needed
    </text>
  </g>

  <rect class="rule" x="40" y="164" width="1120" height="3" rx="1.5" fill="__PRIMARY__" opacity="0.85"/>
  <g clip-path="url(#ruleClip)">
    <rect class="sheen" x="40" y="164" width="160" height="3" fill="url(#sheenG)"/>
  </g>
{''.join(tiles)}
{''.join(badge_svg)}
</svg>
"""
    return paint(template, p)


# --------------------------------------------------------------------------- #
# 2. Architecture / dataflow
# --------------------------------------------------------------------------- #

def architecture(p: dict, _s: dict) -> str:
    """The dataflow diagram. Deliberately carries NO figures from summary.json.

    It used to print row counts and the date window, which meant regenerating the
    sample data rewrote this asset too - so a data refresh produced a diff in a
    diagram whose actual subject, the shape of the pipeline, had not changed. The
    numbers belong in the README's own tables, where they are read in context.

    The `_s` parameter exists only because main() calls every builder with the same
    signature. If you find yourself reaching for it, the figure you want almost
    certainly belongs in the README text instead.
    """
    def box(x, y, w, h, title, lines, accent, dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        # 10.5 renders at 7.9 in GitHub's ~900px column, which is below the
        # ~11px floor the rest of these assets hold to. It cannot be raised
        # here: six boxes across 1200px leave ~218px of text width, and the
        # longest body line already needs 189 of it. Clearing 11px rendered
        # means 14.7 source, so the boxes would have to be half again as wide.
        # That is a layout change - fewer columns, or two rows - not a size one.
        body = "".join(
            f'<text x="{x + 16}" y="{y + 50 + i * 17}" font-family="__MONO__" font-size="10.5" '
            f'fill="__MUTED__">{esc(t)}</text>'
            for i, t in enumerate(lines)
        )
        return f"""
  <g class="node">
    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="__CARD__"
          stroke="__BORDER__"{dash}/>
    <rect x="{x}" y="{y}" width="{w}" height="3" rx="1.5" fill="__{accent.upper()}__"/>
    <text x="{x + 16}" y="{y + 28}" font-family="__FONT__" font-size="16"
          font-weight="600" fill="__TEXT__">{esc(title)}</text>
    {body}
  </g>"""

    def flow(x1, y1, x2, y2, delay=0.0, colour="primary"):
        mid = (x1 + x2) / 2
        d = f"M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"
        return f"""
  <path d="{d}" fill="none" stroke="__BORDER__" stroke-width="1.5"/>
  <path class="dash" style="animation-delay:{delay}s" d="{d}" fill="none"
        stroke="__{colour.upper()}__" stroke-width="2" stroke-linecap="round"
        stroke-dasharray="9 15"/>
  <polygon points="{x2},{y2} {x2 - 7},{y2 - 4.5} {x2 - 7},{y2 + 4.5}" fill="__BORDER__"/>"""

    boxes = (
        box(36, 46, 250, 104, "INFORMATION_SCHEMA", [
            "JOBS_BY_PROJECT, per region", "bytes billed · labels · errors",
            "one row per job", "filtered to on-demand jobs",
        ], "primary")
        + box(36, 176, 250, 104, "dbt_artifacts", [
            "model + test + snapshot execs", "invocations · models · tags",
            "adapter_response → job_id",
        ], "violet")
        + box(36, 300, 250, 104, "sample-data/*.csv", [
            "the committed synthetic month",
            "generated from a fixed seed",
            "no cloud account required",
        ], "good", dashed=True)
        + box(366, 150, 214, 150, "Power Query", [
            "p_DataSource switches", "every partition between",
            "BigQuery and CSV.", "", "Types stated per table -",
            "CSV carries none.",
        ], "cyan")
        + box(654, 128, 236, 194, "Semantic model", [
            "Fact  ← one row per job", "dbt_node_executions",
            "dbt_invocations · dbt_models", "dim_Date · dim_Region_Cost", "",
            "cost restricted to top-level", "jobs (parent_job_id BLANK)",
            "", "region → usd_per_tib rate",
        ], "warn")
        + box(958, 150, 206, 150, "Report", [
            "Query & Usage Insights", "DBT Jobs", "DBT Jobs · Nodes Failed",
            "two Explore tabs", "", "drillthrough + tooltips",
        ], "good")
    )

    flows = (
        flow(286, 98, 366, 200, 0.0)
        + flow(286, 228, 366, 225, 0.35, "violet")
        + flow(286, 350, 366, 250, 0.7, "good")
        + flow(580, 225, 654, 225, 1.0, "cyan")
        + flow(890, 225, 958, 225, 1.35, "warn")
    )

    template = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 402" width="1200" height="402" role="img" aria-label="How the model is wired, from BigQuery and dbt metadata through to the report pages">
  <style>
    .node {{ animation: fade .5s ease-out both; }}
    .node:nth-of-type(1) {{ animation-delay:.05s }}
    .node:nth-of-type(2) {{ animation-delay:.12s }}
    .node:nth-of-type(3) {{ animation-delay:.19s }}
    .node:nth-of-type(4) {{ animation-delay:.26s }}
    .node:nth-of-type(5) {{ animation-delay:.33s }}
    .node:nth-of-type(6) {{ animation-delay:.40s }}
    .dash {{ animation: march 1.1s linear infinite; }}
    /* Transform only - see the note in the hero. */
    @keyframes fade {{ from {{ transform: translateY(8px) }}
                       to   {{ transform: translateY(0) }} }}
    @keyframes march {{ from {{ stroke-dashoffset: 48 }} to {{ stroke-dashoffset: 0 }} }}
    @media (prefers-reduced-motion: reduce) {{ .node,.dash {{ animation: none }} }}
  </style>
  <rect width="1200" height="402" fill="__BG__"/>
  <!-- No title inside the asset. The README carries the "How it is wired" heading
       immediately above it, so painting it again repeated the words and spent 28px
       of height on them. Everything below shifts up by that 28 via one transform
       rather than by rewriting every coordinate in the diagram. -->
  <g transform="translate(0,-28)">
{flows}
{boxes}
  <text x="366" y="348" font-family="__MONO__" font-size="15" fill="__FAINT__">
    p_DataSource = "SampleCSV" | "BigQuery"
  </text>
  </g>
</svg>
"""
    return paint(template, p)


# --------------------------------------------------------------------------- #
# 3. Attribution branches
# --------------------------------------------------------------------------- #

def attribution(p: dict, s: dict) -> str:
    a = s["attribution"]
    total = s["spend_dbt"]
    rows = [
        ("Node (dbt metadata)", a["node_metadata"], "good",
         "matched to a recorded node execution by job id"),
        ("Node (label)", a["node_label"], "warn",
         "post-hook jobs dbt never records - labels only"),
        ("Run-level", a["run_level"], "faint",
         "a dbt run with no identifiable node"),
    ]

    max_usd = max(r[1]["usd"] for r in rows) or 1
    bars = []
    y = 104
    for i, (label, vals, colour, note) in enumerate(rows):
        pct = vals["usd"] / total * 100 if total else 0
        w = max(3, round(vals["usd"] / max_usd * 560))
        bars.append(f"""
  <g class="row" style="animation-delay:{0.15 + i * 0.14:.2f}s">
    <text x="36" y="{y + 2}" font-family="__FONT__" font-size="17" font-weight="600"
          fill="__TEXT__">{esc(label)}</text>
    <text x="36" y="{y + 26}" font-family="__FONT__" font-size="15"
          fill="__MUTED__">{esc(note)}</text>
    <rect x="420" y="{y - 14}" width="560" height="24" rx="6" fill="__TRACK__"/>
    <rect class="bar" style="animation-delay:{0.3 + i * 0.14:.2f}s;--w:{w}px"
          x="420" y="{y - 14}" height="24" rx="6" fill="__{colour.upper()}__"/>
    <text x="1164" y="{y + 2}" text-anchor="end" font-family="__MONO__" font-size="16.5"
          font-weight="600" fill="__TEXT__">${vals['usd']:,.2f}</text>
    <text x="1164" y="{y + 26}" text-anchor="end" font-family="__MONO__" font-size="15"
          fill="__MUTED__">{pct:.1f}%  ·  {vals['jobs']:,} jobs</text>
  </g>""")
        y += 86

    template = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 372" width="1200" height="372" role="img" aria-label="How dbt spend is attributed across three branches">
  <style>
    /* The bar's resting width is its real width, and the animation grows into it.
       Written the other way round - base width 0, filled in by the animation - a
       renderer that skips CSS shows three empty tracks. */
    .row {{ animation: slide .5s ease-out both; }}
    .bar {{ width: var(--w); animation: grow .85s cubic-bezier(.2,.7,.3,1); }}
    @keyframes slide {{ from {{ transform: translateX(-8px) }}
                        to   {{ transform: translateX(0) }} }}
    @keyframes grow {{ from {{ width: 0 }} to {{ width: var(--w) }} }}
    @media (prefers-reduced-motion: reduce) {{
      .row,.bar {{ animation: none }}
    }}
  </style>
  <rect width="1200" height="372" fill="__BG__"/>
  <text x="36" y="36" font-family="__FONT__" font-size="15.5" font-weight="600"
        fill="__FAINT__" letter-spacing="1.4">WHERE THE dbt BILL COMES FROM</text>
  <text x="36" y="66" font-family="__FONT__" font-size="17.5" fill="__MUTED__">
    ${total:,.2f} of dbt spend, split by how confidently each job ties back to a node.
  </text>
{''.join(bars)}
  <rect x="36" y="322" width="1128" height="1" fill="__BORDER__"/>
  <text x="36" y="352" font-family="__FONT__" font-size="15.5" fill="__MUTED__">
    Drop the label branch and ${a['node_label']['usd']:,.2f} of the bill - {a['node_label']['usd'] / total * 100:.1f}% - becomes unattributable. That is the case for this model.
  </text>
</svg>
"""
    return paint(template, p)


# --------------------------------------------------------------------------- #
# 4. Live-report call to action
# --------------------------------------------------------------------------- #

def cta(p: dict, s: dict) -> str:
    """The big 'open the live report' button.

    A shields.io badge was too small to notice at a normal scroll speed, and
    GitHub strips <iframe> so the report cannot be embedded in the page at all -
    this link is the only route to it, which makes it worth drawing properly.
    """
    # The canvas is taller than the button so the transparent margin travels with
    # the asset. GitHub strips inline style attributes from README HTML, so
    # spacing cannot be set at the point of use - baking it in is the only way to
    # guarantee the button is not crowded by the text above and below it.
    template = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 660 152" width="660" height="152" role="img" aria-label="Open the live report in the Power BI service">
  <style>
    /* The sheen and ring are the only motion, and they ride over a button that
       is already fully drawn - strip the CSS and nothing is lost but polish. */
    .sheen { animation: sweep 3.8s ease-in-out infinite 1s }
    .ring  { animation: pulse 2.6s ease-in-out infinite }
    @keyframes sweep { 0% { transform: translateX(-260px) }
                       55%,100% { transform: translateX(700px) } }
    @keyframes pulse { 0%,100% { opacity: .34 } 50% { opacity: .9 } }
    @media (prefers-reduced-motion: reduce) { .sheen,.ring { animation: none } }
  </style>
  <defs>
    <linearGradient id="btn" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%"   stop-color="__PRIMARY__"/>
      <stop offset="100%" stop-color="__VIOLET__"/>
    </linearGradient>
    <linearGradient id="gloss" x1="0" x2="1">
      <stop offset="0%"   stop-color="#FFFFFF" stop-opacity="0"/>
      <stop offset="50%"  stop-color="#FFFFFF" stop-opacity="0.30"/>
      <stop offset="100%" stop-color="#FFFFFF" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="btnClip"><rect x="4" y="6" width="652" height="76" rx="16"/></clipPath>
    <filter id="lift" x="-20%" y="-40%" width="140%" height="200%">
      <feDropShadow dx="0" dy="4" stdDeviation="7"
                    flood-color="__PRIMARY__" flood-opacity="__CTASHADOW__"/>
    </filter>
  </defs>

  <!-- Deliberately no background rect. The full-width banners can paint their
       own because they span the column, but a 660px button that painted
       __BG__ would show as an off-colour box on any GitHub theme whose canvas
       is not exactly white or #0D1117 - "dark dimmed" being the obvious one.
       Transparent composites onto whatever the page actually is.

       Everything sits in one translate so the button keeps its own coordinates
       and only the offset decides how much clear space sits above it. -->
  <g transform="translate(0,30)">
    <rect x="4" y="6" width="652" height="76" rx="16" fill="url(#btn)" filter="url(#lift)"/>

    <g clip-path="url(#btnClip)">
      <rect class="sheen" x="0" y="6" width="150" height="76" fill="url(#gloss)"/>
    </g>

    <circle class="ring" cx="46" cy="44" r="21" fill="none" stroke="#FFFFFF"
            stroke-opacity="0.55" stroke-width="2"/>
    <circle cx="46" cy="44" r="15.5" fill="#FFFFFF" fill-opacity="0.18"/>
    <path d="M 41 36.5 L 53 44 L 41 51.5 Z" fill="#FFFFFF"/>

    <text x="84" y="42" font-family="__FONT__" font-size="21" font-weight="700"
          fill="#FFFFFF" letter-spacing="-0.2">Open the live report</text>
    <text x="84" y="63" font-family="__FONT__" font-size="13.5"
          fill="#FFFFFF" fill-opacity="0.88">Power BI service  &#183;  runs in your browser  &#183;  no sign-in, no cloud account</text>
  </g>
</svg>
"""
    return paint(template, p)


# --------------------------------------------------------------------------- #
# 5. Section banners
# --------------------------------------------------------------------------- #

# Geometric glyphs rather than emoji: an SVG served through GitHub's image proxy
# has no network access and cannot load a font, so an emoji would fall back to
# whatever the renderer happens to have - or to a blank box. Drawn shapes look
# the same everywhere.
GLYPHS = {
    "magnifier": """
    <circle cx="0" cy="-3" r="15" fill="none" stroke="__ACCENT__" stroke-width="4"/>
    <line x1="11" y1="8" x2="21" y2="18" stroke="__ACCENT__" stroke-width="5"
          stroke-linecap="round"/>""",
    "layers": """
    <rect x="-17" y="7"   width="34" height="8" rx="2" fill="__ACCENT__" opacity="0.45"/>
    <rect x="-17" y="-4"  width="34" height="8" rx="2" fill="__ACCENT__" opacity="0.72"/>
    <rect x="-17" y="-15" width="34" height="8" rx="2" fill="__ACCENT__"/>""",
    "play": """
    <circle cx="0" cy="0" r="16" fill="none" stroke="__ACCENT__" stroke-width="4"/>
    <path d="M -5.5 -8 L 9 0 L -5.5 8 Z" fill="__ACCENT__"/>""",
}

SECTIONS = {
    "section-query": {
        "eyebrow": "PART 1",
        "title": "Query & Usage Insights",
        "subtitle": "Every BigQuery job - who spends, on what, how long, and how much was cache.",
        "note": "no dbt required",
        "accent": "primary",
        "glyph": "magnifier",
        "chips": ["Performance Analysis", "Explore", "Query pattern drillthrough"],
    },
    "section-dbt": {
        "eyebrow": "PART 2",
        "title": "dbt on BigQuery",
        "subtitle": "What the transformation layer costs, how much it builds, and whether it is healthy.",
        "note": "needs dbt_artifacts",
        "accent": "violet",
        "glyph": "layers",
        "chips": ["Overview", "Nodes failed", "Explore"],
    },
    "section-setup": {
        "eyebrow": "PART 3",
        "title": "Run it yourself",
        "subtitle": "Clone it, point it at the sample data or your own BigQuery, and refresh.",
        "note": "offline by default",
        "accent": "good",
        "glyph": "play",
        "chips": ["Quickstart", "Parameters", "Prerequisites"],
    },
}


def section(p: dict, s: dict, spec: dict) -> str:
    accent = p[spec["accent"]]

    chips = []
    cx = 92
    for i, c in enumerate(spec["chips"]):
        cw = 26 + len(c) * 8.6
        chips.append(f"""
  <g class="chip" style="animation-delay:{0.30 + i * 0.09:.2f}s">
    <rect x="{cx:.0f}" y="132" width="{cw:.0f}" height="33" rx="16.5"
          fill="__CARD__" stroke="__BORDER__"/>
    <circle cx="{cx + 15:.0f}" cy="148.5" r="4" fill="{accent}"/>
    <text x="{cx + 28:.0f}" y="154" font-family="__FONT__" font-size="15.5"
          fill="__MUTED__">{esc(c)}</text>
  </g>""")
        cx += cw + 9

    note_w = 24 + len(spec["note"]) * 8.4

    template = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 196" width="1200" height="196" role="img" aria-label="{esc(spec['eyebrow'])} - {esc(spec['title'])}">
  <style>
    /* Same rule as the hero: entrance animations only ever move things. The
       resting state of every element is its final state, so the banner is fully
       legible in a renderer that ignores CSS. */
    /* The glyph is drawn centred on its own origin, so scaling about 0 0 scales
       about its centre. Left as the CSS default of 50% 50% the reference box
       differs between renderers and the glyph drifts as it scales. */
    .glyph {{ animation: pop .5s cubic-bezier(.2,.8,.3,1); transform-origin: 0 0 }}
    .txt {{ animation: slidein .45s ease-out both }}
    .chip {{ animation: slidein .4s ease-out both }}
    .edge {{ animation: stretch .7s cubic-bezier(.2,.7,.3,1) }}
    @keyframes pop {{ from {{ transform: scale(.82) }} to {{ transform: scale(1) }} }}
    @keyframes slidein {{ from {{ transform: translateX(-10px) }}
                          to   {{ transform: translateX(0) }} }}
    @keyframes stretch {{ from {{ height: 0 }} to {{ height: 196px }} }}
    @media (prefers-reduced-motion: reduce) {{
      .glyph,.txt,.chip,.edge {{ animation: none }}
    }}
  </style>
  <defs>
    <linearGradient id="wash" x1="0" x2="1">
      <stop offset="0%"   stop-color="{accent}" stop-opacity="__WASH__"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <rect width="1200" height="196" fill="__BG__"/>
  <rect width="640" height="196" fill="url(#wash)"/>
  <rect class="edge" x="0" y="0" width="6" height="196" fill="{accent}"/>

  <!-- Two nested groups on purpose. A CSS `transform` in the animation would
       override the SVG transform attribute on the same element, so animating
       scale on the positioned group snaps the glyph to the origin. The outer
       group positions, the inner one animates. -->
  <g transform="translate(52,70)">
    <g class="glyph">{GLYPHS[spec['glyph']].replace('__ACCENT__', accent)}</g>
  </g>

  <g class="txt">
    <text x="92" y="46" font-family="__FONT__" font-size="17" font-weight="700"
          fill="{accent}" letter-spacing="2">{esc(spec['eyebrow'])}</text>
  </g>
  <g class="txt" style="animation-delay:.06s">
    <text x="92" y="86" font-family="__FONT__" font-size="31" font-weight="700"
          fill="__TEXT__" letter-spacing="-0.5">{esc(spec['title'])}</text>
  </g>
  <g class="txt" style="animation-delay:.12s">
    <text x="92" y="116" font-family="__FONT__" font-size="17.5"
          fill="__MUTED__">{esc(spec['subtitle'])}</text>
  </g>

  <g class="txt" style="animation-delay:.18s">
    <rect x="{1164 - note_w:.0f}" y="36" width="{note_w:.0f}" height="32" rx="16"
          fill="{accent}" fill-opacity="0.14" stroke="{accent}" stroke-opacity="0.45"/>
    <text x="{1164 - note_w / 2:.0f}" y="57" text-anchor="middle"
          font-family="__FONT__" font-size="15" font-weight="600"
          fill="{accent}">{esc(spec['note'])}</text>
  </g>
{''.join(chips)}
</svg>
"""
    return paint(template, p)


# --------------------------------------------------------------------------- #

def main() -> None:
    if not SUMMARY.exists():
        raise SystemExit(
            f"{SUMMARY} not found - run scripts/generate_sample_data.py first."
        )
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    ASSETS.mkdir(parents=True, exist_ok=True)

    builders = {"hero": hero, "cta": cta, "architecture": architecture,
                "attribution": attribution}
    for name, spec in SECTIONS.items():
        builders[name] = partial(section, spec=spec)
    for name, fn in builders.items():
        for palette in (LIGHT, DARK):
            out = ASSETS / f"{name}-{palette['name']}.svg"
            # write_bytes so no BOM can appear - Power BI and GitHub both dislike it.
            out.write_bytes(fn(palette, s).encode("utf-8"))
            print(f"  wrote {out.relative_to(ROOT)}  {out.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
