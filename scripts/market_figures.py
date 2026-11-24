"""Evidence-linked, offline SVG data figures for real market serials.

Figures supplement already-published text without rewriting forecasts. Values
are editorially supplied from cited documents, never fetched or extrapolated by
a paid service. Desktop/mobile SVGs and HTML captions share one validated input.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DATA_FILE = Path("content/market_figures.json")
DESKTOP_SIZE = (940, 372)
MOBILE_SIZE = (440, 436)
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
STATES = {
    "実績": "#28786e",
    "会社予想": "#a87a39",
    "公開資料から計算": "#536b9a",
    "資料記載の要因": "#28786e",
    "差分（計算）": "#a87a39",
}


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Figure date must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid figure date") from exc
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical figure date")
    return parsed


def load_market_figures(base: Path, serials: list[dict], *, today=None) -> dict[str, list[dict]]:
    """Validate source, dates, units and placement before publishing any figure."""
    filepath = Path(base) / DATA_FILE
    if not filepath.is_file():
        return {}
    data = json.loads(filepath.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Market figures must be a list")
    published = {
        e["slug"]: e
        for series in serials if series.get("kind") == "analysis"
        for e in series["episodes"]
    }
    now = today or datetime.now(ZoneInfo("Asia/Tokyo")).date()
    figures: dict[str, list[dict]] = {}
    seen = set()
    for figure in data:
        if not isinstance(figure, dict):
            raise ValueError("Invalid market figure")
        slug = figure.get("episode_slug")
        key = figure.get("id")
        if (slug not in published or not isinstance(key, str)
                or not SLUG.fullmatch(key) or (slug, key) in seen):
            raise ValueError("Unknown episode, invalid figure ID or duplicate")
        seen.add((slug, key))
        episode = published[slug]
        section = figure.get("after_section")
        if type(section) is not int or not 1 <= section <= len(episode["sections"]):
            raise ValueError("Figure placement must follow a published section")
        if figure.get("kind") not in ("bars", "composition"):
            raise ValueError("Unsupported figure kind")
        for field in ("title", "note", "unit", "source_title", "source_url"):
            value = figure.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 250:
                raise ValueError("Figure missing or oversized " + field)
        if len(figure["unit"]) > 10 or len(figure["title"]) > 55:
            raise ValueError("Figure title or unit too long")
        source_url = figure["source_url"]
        parsed = urlsplit(source_url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password
                or source_url not in {s["url"] for s in episode["sources"]}):
            raise ValueError("Figure must cite an existing episode HTTPS source")
        source_date = _day(figure.get("source_published_on"))
        checked = _day(figure.get("checked_on"))
        episode_day = _day(episode["published_at"])
        if source_date > episode_day or checked < source_date or checked > now:
            raise ValueError("Figure evidence cannot be future-dated")
        values = figure.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError("Figures compare exactly two sourced quantities")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Invalid figure quantity")
            if (not isinstance(value.get("label"), str)
                    or not 1 <= len(value["label"]) <= 34
                    or value.get("state") not in STATES):
                raise ValueError("Figure requires label and data state")
            amount = value.get("value")
            if type(amount) not in (int, float) or not 0 <= amount <= 10**9:
                raise ValueError("Figure quantity must be a finite nonnegative number")
            if type(amount) is float and (amount != amount or amount == float("inf")):
                raise ValueError("Figure quantity must be finite")
        if figure["kind"] == "composition":
            total = figure.get("total")
            if type(total) not in (int, float) or not 0 < total <= 10**9:
                raise ValueError("Composition requires a positive total")
            if abs(sum(v["value"] for v in values) - total) > 0.000001:
                raise ValueError("Composition quantities must sum to cited total")
            if not isinstance(figure.get("total_label"), str) or not figure["total_label"]:
                raise ValueError("Composition requires a total label")
        elif "total" in figure:
            raise ValueError("Unrelated total in comparison")
        figures.setdefault(slug, []).append(figure)
        if len(figures[slug]) > 3:
            raise ValueError("No more than three figures per episode")
    for group in figures.values():
        group.sort(key=lambda f: (f["after_section"], f["id"]))
    return figures


def _amount(value):
    if type(value) not in (int, float):
        raise ValueError("Market quantity must be a number")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Market quantity must be finite")
    text = format(number, ",f")
    # Strip fractional zeroes only: 1,600 must never become 1,6.
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_market_numbers(figures, *, sources=()):
    """A compact, sourced reading of the same inputs used by the figures."""
    if not figures:
        return ""
    source_by_url = {source["url"]: source for source in sources}
    groups = []
    for figure in figures:
        unit = escape(figure["unit"])
        source_url = figure["source_url"]
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Market table requires its cited HTTPS source")
        source = source_by_url.get(source_url, {})
        source_title = figure.get("source_title") or source.get("title") or "出典を見る"
        observed = figure.get("observed_at")
        if observed:
            stamp = datetime.fromisoformat(observed)
            if stamp.tzinfo is None:
                raise ValueError("Market observation must have timezone")
            date_label = "観測"
            date_text = stamp.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
            date_value = observed
        else:
            date_value = figure.get("source_published_on") or source.get("published_on")
            if not date_value:
                raise ValueError("Market table requires its source date")
            _day(date_value)
            date_label, date_text = "資料公表", date_value
        rows = []
        for value in figure["values"]:
            state = value["state"]
            if state not in STATES:
                raise ValueError("Market table requires an explicit data state")
            rows.append(
                f'<tr><th scope="row">{escape(value["label"])}</th>'
                f'<td class="market-data-number">{escape(_amount(value["value"]))}<span>{unit}</span></td>'
                f'<td><span class="market-data-state">{escape(state)}</span></td></tr>'
            )
        if not rows:
            raise ValueError("Market table requires sourced values")
        groups.append(
            '<tbody class="market-data-group">'
            f'<tr><th scope="rowgroup" colspan="3">{escape(figure["title"])}</th></tr>'
            + "".join(rows)
            + f'<tr><td colspan="3" class="market-data-source">{date_label} '
            f'<time datetime="{escape(date_value, quote=True)}">{escape(date_text)}</time> · '
            f'<a href="{escape(source_url, quote=True)}" target="_blank" rel="noopener noreferrer external">'
            f'{escape(source_title)}</a></td></tr></tbody>'
        )
    return (
        '<section class="market-data-summary" aria-labelledby="market-data-summary-title">'
        '<h2 id="market-data-summary-title">数字の要点</h2>'
        '<table class="market-data-table" aria-labelledby="market-data-summary-title">'
        '<thead><tr><th scope="col">項目</th><th scope="col">数値</th>'
        '<th scope="col">区分</th></tr></thead>' + "".join(groups) + '</table></section>'
    )


def figure_description(figure: dict) -> str:
    items = "、".join(
        f'{v["label"]}は{_amount(v["value"])}{figure["unit"]}（{v["state"]}）'
        for v in figure["values"]
    )
    if figure["kind"] == "composition":
        items += f'。合計{_amount(figure["total"])}{figure["unit"]}'
    return f'{figure["title"]}。{items}。{figure["note"]}'


def render_market_svg(figure: dict, *, mobile=False) -> str:
    """Zero-baseline, self-contained chart. No script or external font/image."""
    width, height = MOBILE_SIZE if mobile else DESKTOP_SIZE
    left = 26 if mobile else 50
    right = width - left
    bar_width = right - left
    unit = escape(figure["unit"])
    # Newspaper figures must tell the reader *what* is being compared.
    # Wrap Japanese titles for the narrow layout without relying on script,
    # foreignObject or a web font.
    title = figure["title"]
    limit = 15 if mobile else 30
    chunks = [title[i:i+limit] for i in range(0, len(title), limit)]
    if len(chunks) > 2:
        # Keep the full verified title in <title>, <desc> and the figure caption.
        chunks = [chunks[0], chunks[1][:limit-1] + "…"]
    title_lines = "".join(
        f'<text x="{left}" y="{49 + i*27}" fill="#202629" '
        f'font-size="{20 if mobile else 26}" font-weight="700">{escape(line)}</text>'
        for i, line in enumerate(chunks)
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" font-family="sans-serif" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<title>{escape(figure["title"])}</title>',
        f'<desc>{escape(figure_description(figure))}</desc>',
        f'<rect width="{width}" height="{height}" rx="6" fill="#f8f8f5"/>',
        f'<rect x="0" y="0" width="5" height="{height}" fill="#495862"/>',
        title_lines,
        f'<text x="{left}" y="{104 if mobile or len(chunks) > 1 else 87}" fill="#495660" font-size="{14 if mobile else 16}">単位：{unit} ／ 0を基準に比較</text>',
    ]
    if figure["kind"] == "bars":
        quantities = [v["value"] for v in figure["values"]]
        signed = min(quantities) < 0
        span = bar_width // 2 if signed else bar_width
        zero = left + span if signed else left
        maximum = max(abs(n) for n in quantities)
        for i, value in enumerate(figure["values"]):
            line = (132 + i * 126) if mobile else (128 + i * 105)
            bar_y = line + 24
            color = STATES[value["state"]]
            amount = value["value"]
            length = round(abs(amount) / maximum * span) if maximum else 0
            x = zero - length if amount < 0 else zero
            label = escape(value["label"] + "｜" + value["state"])
            parts.extend([
                f'<text x="{left}" y="{line}" font-size="{17 if mobile else 20}" fill="#263936">{label}</text>',
                f'<text x="{right}" y="{line+26}" text-anchor="end" font-size="{18 if mobile else 20}" font-weight="700" fill="#163934">{escape(_amount(value["value"]))}</text>',
                f'<rect x="{left}" y="{bar_y+22}" width="{bar_width}" height="{28 if mobile else 30}" rx="5" fill="#dce5df"/>',
                f'<rect x="{x}" y="{bar_y+22}" width="{length}" height="{28 if mobile else 30}" rx="5" fill="{color}"/>',
            ])
            if signed:
                parts.append(f'<line x1="{zero}" x2="{zero}" y1="{bar_y+20}" y2="{bar_y+54}" stroke="#495660"/>')
                parts.append(f'<text x="{zero}" y="{bar_y+16}" text-anchor="middle" font-size="13" fill="#495660">0</text>')
    else:
        total = figure["total"]
        y = 148 if mobile else 143
        x = left
        for value in figure["values"]:
            piece = round(bar_width * value["value"] / total)
            parts.append(f'<rect x="{x}" y="{y}" width="{piece}" height="{57 if mobile else 75}" fill="{STATES[value["state"]]}"/>')
            x += piece
        if mobile:
            parts.append(f'<text x="{left}" y="120" fill="#163934" font-size="19">{escape(figure["total_label"])}：{_amount(total)}{unit}</text>')
        else:
            parts.append(f'<text x="{left}" y="124" fill="#163934" font-size="21">{escape(figure["total_label"])}：{_amount(total)}{unit}</text>')
        for i, value in enumerate(figure["values"]):
            y0 = (259 + i*66) if mobile else 277
            x0 = left if mobile else left + i*440
            parts.extend([
                f'<rect x="{x0}" y="{y0-18}" width="18" height="18" fill="{STATES[value["state"]]}"/>',
                f'<text x="{x0+28}" y="{y0-3}" fill="#253a36" font-size="{17 if mobile else 18}">{escape(value["label"])}：{escape(_amount(value["value"]))}{unit}</text>',
            ])
    parts.append("</svg>")
    return "".join(parts)


def _figure_path(series: dict, episode: dict, figure: dict, *, mobile=False) -> str:
    name = figure["id"] + ("-mobile" if mobile else "") + ".svg"
    return "serials/" + series["slug"] + "/figures/" + episode["slug"] + "/" + name


def render_market_picture(figure: dict, desktop_path: str, mobile_path: str) -> str:
    def versioned_url(path, *, mobile):
        svg = render_market_svg(figure, mobile=mobile)
        digest = sha256(svg.encode("utf-8")).hexdigest()[:12]
        return f'/{escape(path, quote=True)}?v={digest}'

    desktop_width, desktop_height = DESKTOP_SIZE
    mobile_width, mobile_height = MOBILE_SIZE
    return (
        '<picture>'
        f'<source media="(max-width:600px)" srcset="{versioned_url(mobile_path, mobile=True)}" '
        f'type="image/svg+xml" width="{mobile_width}" height="{mobile_height}">'
        f'<img src="{versioned_url(desktop_path, mobile=False)}" width="{desktop_width}" height="{desktop_height}" '
        f'loading="lazy" decoding="async" alt="{escape(figure_description(figure), quote=True)}">'
        '</picture>'
    )


def render_market_figure(series: dict, episode: dict, figure: dict) -> str:
    picture = render_market_picture(figure, _figure_path(series, episode, figure),
                                    _figure_path(series, episode, figure, mobile=True))
    source = escape(figure["source_url"], quote=True)
    return (
        f'<figure class="market-figure">{picture}<figcaption>'
        f'<strong>{escape(figure["title"])}</strong>'
        f'<p>{escape(figure["note"])}</p>'
        f'<p class="market-figure-meta">単位：{escape(figure["unit"])} ／ '
        f'資料公表：<time datetime="{figure["source_published_on"]}">{figure["source_published_on"]}</time>'
        f' ／ 図解確認：<time datetime="{figure["checked_on"]}">{figure["checked_on"]}</time></p>'
        f'<a href="{source}" target="_blank" rel="noopener noreferrer external">'
        f'出典：{escape(figure["source_title"])}</a>'
        '</figcaption></figure>'
    )


def write_market_figures(series: dict, episode: dict, figures: list[dict], write) -> None:
    for figure in figures:
        for mobile in (False, True):
            write(_figure_path(series, episode, figure, mobile=mobile),
                  render_market_svg(figure, mobile=mobile))
