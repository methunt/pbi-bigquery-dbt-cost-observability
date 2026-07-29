"""Turn raw Power BI Desktop captures into the page stills and GIF tours in the README.

Input:  screenshots/_raw/<Page display name>.png - raw captures, gitignored,
        written by the Desktop bridge with the report in View -> Fit to page:

          powerbi-desktop screenshot-all --scale 3 \
            --output-dir screenshots/_raw --settle 9000

Output: screenshots/<kebab-name>.png   cropped, URL-safe stills  (committed)
        assets/tour-*.gif              per-section tab tours     (committed)

The raw captures stay out of git: they carry Desktop's own window chrome, their
names contain spaces and a "·" that make for awkward URLs, and they are a
by-product of a local tool rather than something a reader needs.

Needs Pillow and NumPy (`pip install Pillow numpy`). It is a README build step,
not part of the model, so nobody cloning the repo to open the report needs it -
the outputs are committed. The data generator stays deliberately stdlib-only.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "screenshots"
RAW = SHOTS / "_raw"
ASSETS = ROOT / "assets"

# Fit-to-page centres the report canvas in Desktop's grey workspace and leaves
# the collapsed Filters rail down the right edge. Both are application chrome,
# not the report, so they are detected and cropped rather than shipped as though
# a reader were meant to interpret them. Hardcoded offsets would go wrong the
# moment the window is resized or the capture scale changes, and page sizes
# differ per page anyway (1750x1420, 1750x2400, 1750x1000, 520x400), so there is
# no single box to hardcode - the content is found by looking for it.
#
# Brightness cannot do the finding: Desktop's workspace grey (246,248,250) is
# the same family as the report's own page background, so a near-white test
# spans workspace, canvas and rail alike. Local contrast separates them cleanly
# instead - workspace and gutter are flat fills with almost no variation, while
# anything carrying text or a chart varies a lot.
MIN_STD = 4.0        # per-column/row std above which it counts as holding content
# Gaps up to this share of the image are bridged, larger ones split. Internal
# gutters between visuals run about 1% of the capture width; the workspace either
# side of the canvas is far more. Set it too high and a short, wide page - the
# drillthrough is 1750x1000 - leaves a gutter narrow enough that the Filters rail
# gets bridged into the crop.
MAX_GAP_FRAC = 0.025
PAD = 6              # keep a hairline of page background so cards do not look sheared

TARGET_WIDTH = 1400    # GIF frame width - readable on GitHub, still small
FRAME_MS = 3200        # long enough to actually read a page before it changes

# (raw capture name from the bridge, committed still name)
PAGES = [
    ("Query & Usage Insights.png",           "query-usage-insights.png"),
    ("Query & Usage Insights · Explore.png", "query-usage-insights-explore.png"),
    ("DBT Jobs.png",                         "dbt-jobs.png"),
    ("DBT Jobs · Nodes Failed.png",          "dbt-jobs-nodes-failed.png"),
    ("DBT Jobs · Explore.png",               "dbt-jobs-explore.png"),
    # The two drillthrough pages are deliberately absent, and the README explains
    # the drillthrough in prose instead. Neither has a meaningful standalone
    # capture: "Query pattern" renders an empty Statement box until you arrive
    # with a selection, and "Query pattern tooltip" is a 520x400 page that only
    # ever appears inside another visual's hover card. Their captures also defeat
    # the crop check below - one fills the frame edge to edge with no gutter to
    # find, the other comes out a fragment - so shipping them would mean shipping
    # the one crop that could not be verified.
]

# Only pages of the same authored size belong in one tour. "DBT Jobs · Nodes
# Failed" is authored 1750x2400 against the others' 1750x1420, so including it
# would force every frame up to its height and leave the rest two-thirds empty.
# It gets its own still in the README instead.
TOURS = [
    ("tour-query-insights.gif",
     ["query-usage-insights.png", "query-usage-insights-explore.png"]),
    ("tour-dbt-jobs.gif",
     ["dbt-jobs.png", "dbt-jobs-explore.png"]),
]


def widest_group(flags, max_gap):
    """Extent of the widest group of content, bridging gaps up to max_gap.

    A plain min/max bounding box would be dragged out to the Filters rail, and a
    single contiguous run is too strict - the flat gutters *between* visuals
    would split one canvas into a dozen fragments. Bridging small gaps but not
    large ones separates the two cases, because the gutter between the canvas and
    the rail is the empty workspace and is far wider than any gutter inside the
    report.

    Returns (start, end_exclusive).
    """
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return 0, 0
    # Split wherever consecutive content indices jump by more than max_gap.
    breaks = np.flatnonzero(np.diff(idx) > max_gap)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    widest = int(np.argmax(ends - starts))
    return int(starts[widest]), int(ends[widest]) + 1


def canvas_box(im):
    """Bounding box of the report content within a Desktop capture."""
    a = np.asarray(im.convert("L"), dtype=np.float32)
    h, w = a.shape

    # Standard deviation, not brightness: workspace grey and the report's own
    # page background are the same family, so brightness cannot tell them apart,
    # while a flat fill and anything carrying text or a chart differ sharply in
    # local variation.
    col_content = a.std(axis=0) >= MIN_STD
    row_content = a.std(axis=1) >= MIN_STD

    c0, c1 = widest_group(col_content, max_gap=max(24, int(w * MAX_GAP_FRAC)))
    r0, r1 = widest_group(row_content, max_gap=max(24, int(h * MAX_GAP_FRAC)))
    if c1 <= c0 or r1 <= r0:
        raise SystemExit("could not locate the report canvas - is the capture blank?")

    return (max(0, c0 - PAD), max(0, r0 - PAD),
            min(w, c1 + PAD), min(h, r1 + PAD))


def declared_aspects():
    """Each page's authored width/height, read from the PBIR definition.

    Used to check the crops. An automatic crop that silently takes half a page is
    the kind of defect that ships, because the output still looks like a
    screenshot - so the crop is verified against what the report actually says
    the page is, rather than trusted.
    """
    pages_dir = next(ROOT.glob("powerbi/*.Report/definition/pages"))
    out = {}
    for page_json in pages_dir.glob("*/page.json"):
        d = json.loads(page_json.read_text(encoding="utf-8"))
        if d.get("width") and d.get("height"):
            out[d["displayName"]] = d["width"] / d["height"]
    return out


# A tight content crop drops the page's own outer margin, so the cropped aspect
# never matches the declared one exactly. This tolerance passes that while still
# failing a crop that lost a row or a column of visuals.
ASPECT_TOLERANCE = 0.12


def crop_stills():
    """Crop each raw capture to the canvas and save under its URL-safe name."""
    aspects = declared_aspects()
    problems = []
    for raw_name, out_name in PAGES:
        raw = RAW / raw_name
        if not raw.exists():
            raise SystemExit(
                f"missing capture: {raw}\n"
                "With the PBIP open in Desktop on View -> Fit to page, run:\n"
                "  powerbi-desktop screenshot-all --scale 3 "
                "--output-dir screenshots/_raw --settle 9000"
            )
        im = Image.open(raw).convert("RGB")
        cropped = im.crop(canvas_box(im))
        out = SHOTS / out_name
        cropped.save(out, optimize=True)

        expected = aspects.get(Path(raw_name).stem)
        got = cropped.width / cropped.height
        flag = ""
        if expected:
            drift = abs(got - expected) / expected
            if drift > ASPECT_TOLERANCE:
                flag = f"  <-- aspect {got:.2f} vs declared {expected:.2f}"
                problems.append(f"{out_name}: {flag.strip()}")
        print(f"  {out_name}: {cropped.width}x{cropped.height}  "
              f"({im.width}x{im.height} raw)  "
              f"{out.stat().st_size / 1024:,.0f} KB{flag}")

    if problems:
        raise SystemExit(
            "crop does not match the declared page shape:\n  "
            + "\n  ".join(problems)
            + "\nThe capture probably was not taken with View -> Fit to page."
        )


def build_tour(out_name, frame_names):
    frames = []
    for n in frame_names:
        im = Image.open(SHOTS / n).convert("RGB")
        scale = TARGET_WIDTH / im.width
        frames.append(im.resize((TARGET_WIDTH, round(im.height * scale)), Image.LANCZOS))

    # Crops differ by a few pixels between pages, so pad to a common height. Guard
    # first: padding is only ever meant to absorb that few-pixel drift, and
    # silently absorbing a genuinely differently-shaped page instead produces a
    # GIF that is mostly empty space.
    h = max(f.height for f in frames)
    if h - min(f.height for f in frames) > 0.05 * h:
        raise SystemExit(
            f"{out_name}: frame heights {[f.height for f in frames]} differ too much - "
            "these pages are not the same authored size, so they do not belong in one tour"
        )
    padded = []
    for f in frames:
        if f.height == h:
            padded.append(f)
        else:
            canvas = Image.new("RGB", (TARGET_WIDTH, h), (255, 255, 255))
            canvas.paste(f, (0, 0))
            padded.append(canvas)

    # One adaptive palette across every frame, so the tab pills and KPI accents
    # do not shift hue as the animation cycles.
    strip = Image.new("RGB", (TARGET_WIDTH, h * len(padded)))
    for i, f in enumerate(padded):
        strip.paste(f, (0, i * h))
    palette = strip.quantize(colors=256, method=Image.MEDIANCUT)

    quantized = [f.quantize(palette=palette, dither=Image.FLOYDSTEINBERG) for f in padded]
    out = ASSETS / out_name
    quantized[0].save(out, save_all=True, append_images=quantized[1:],
                      duration=FRAME_MS, loop=0, optimize=True, disposal=1)
    print(f"  {out_name}: {len(quantized)} frames, {TARGET_WIDTH}x{h}, "
          f"{out.stat().st_size / 1024:,.0f} KB")


def main():
    ASSETS.mkdir(exist_ok=True)
    print("stills:")
    crop_stills()
    print("tours:")
    for out_name, frame_names in TOURS:
        build_tour(out_name, frame_names)


if __name__ == "__main__":
    main()
