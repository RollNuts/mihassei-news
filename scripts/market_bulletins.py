"""Evidence-based market dispatches, separate from immutable forecasts.

The dispatch stream is opt-in data, never live quotes or trade recommendations.
Static site generation requires sourced prices and explicit market context.
No network I/O, JavaScript API, external images or paid market data.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from email.utils import format_datetime
from hashlib import sha256
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
import json
import math
import re

try:
    from .market_figures import render_market_svg, figure_description, render_market_numbers, STATES
    from .reader_comments import render_reader_comments
    from .market_outlooks import view_models, forecast_reader_statement, render_market_accuracy
    from .market_sharing import render_market_share
except ImportError:
    from market_figures import render_market_svg, figure_description, render_market_numbers, STATES
    from reader_comments import render_reader_comments
    from market_outlooks import view_models, forecast_reader_statement, render_market_accuracy
    from market_sharing import render_market_share

FILE = Path("content/market_bulletins.json")
MARKET_URL = "/serials/market-lab/"
FEED_URL = "/feeds/market-bulletins.xml"
SITE = "https://veripsa.com"
SLOTS = {
    "am": ("08:30", "寄り付き前の全体市況"),
    "open": ("09:40", "寄り付き後の全体市況"),
    "noon": ("12:40", "後場の全体市況"),
    "close": ("15:50", "日本株の急騰・急落"),
}
KEY = re.compile(r"20\d{2}-\d{2}-\d{2}-(?:am|open|noon|close)\Z")
CODE = re.compile(r"(?:\d{4}|\d{3}[A-Z])\Z")
US_CODE = re.compile(r"[A-Z]{1,5}(?:[.-][A-Z])?\Z")
STOCK_MARKETS = {
    "東証": ("JPY", "Asia/Tokyo"),
    "NASDAQ": ("USD", "America/New_York"),
    "NYSE": ("USD", "America/New_York"),
}
STOCK_SESSIONS = {"東証": (time(9), time(15, 30)),
                  "NASDAQ": (time(9, 30), time(16)),
                  "NYSE": (time(9, 30), time(16))}
JST = timedelta(hours=9)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Market dispatch needs actual JST timestamp")
    try:
        d = datetime.fromisoformat(value)
    except (ValueError, TypeError) as e:
        raise ValueError("Invalid market dispatch timestamp") from e
    if d.utcoffset() != JST or d.isoformat(timespec="seconds") != value:
        raise ValueError("Market dispatch timestamp must be JST ISO8601 seconds")
    return d


def observation_window(report):
    """Use only recorded observation times; publication is not a market quote."""
    published = _timestamp(report["published_at"])
    observed = []
    for group in ("figures", "conditions", "picks"):
        for item in report.get(group, []):
            if not item.get("observed_at"):
                continue
            stamp = _timestamp(item["observed_at"])
            if stamp > published:
                raise ValueError("Market observation cannot follow publication")
            observed.append(stamp)
    return (min(observed), max(observed)) if observed else None


def render_market_freshness(report, *, include_publication=True):
    """A static, source-backed clock and a route to the exchange's current data."""
    if not report.get("published_at"):
        return ""
    published = _timestamp(report["published_at"])
    window = observation_window(report)

    def clock(stamp):
        return (f'<time datetime="{escape(stamp.isoformat(), quote=True)}">'
                f'{escape(stamp.strftime("%Y-%m-%d %H:%M"))}</time>')

    parts = ["最終公開：" + clock(published) + " JST"] if include_publication else []
    if window:
        start, end = window
        label = clock(start) + (" 〜 " + clock(end) if start != end else "")
        parts.append(("掲載データの観測日時：" if report.get("coverage") == "investor" else "観測値：") + label + " JST")
    index_label = "日本株指数を確認（JPX）" if report.get("coverage") == "investor" else "現在の指数（JPX）"
    parts.append('<a href="https://www.jpx.co.jp/markets/indices/realvalues/" '
                 f'target="_blank" rel="noopener noreferrer external">{index_label}</a>')
    return '<p class="market-paper-small market-reader-freshness">' + '<br>'.join(parts) + '</p>'


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Market source date is required")
    try:
        d = date.fromisoformat(value)
    except ValueError as e:
        raise ValueError("Invalid source date") from e
    if d.isoformat() != value:
        raise ValueError("Invalid source date")
    return d


def _txt(value, name, minimum=1, maximum=800):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError("Invalid market dispatch " + name)
    return value


def _url(value):
    v = urlsplit(_txt(value, "source URL"))
    if v.scheme != "https" or not v.hostname or v.username or v.password:
        raise ValueError("Market dispatch source must be HTTPS")
    return value


def _amount(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Market figures require nonnegative finite quantities")
    return value


def bulletin_url(item):
    return MARKET_URL + "briefings/" + item["slug"] + "/"


def _validate_investor_picks(picks, source_urls, published):
    """Validate sourced stock examples without imposing a mover quota."""
    if not isinstance(picks, list) or len(picks) > 3:
        raise ValueError("Investor coverage allows zero to three stock examples")
    seen = set()
    for pick in picks:
        if not isinstance(pick, dict):
            raise ValueError("Invalid market stock example")
        exchange, code = pick.get("exchange"), pick.get("code")
        if not isinstance(exchange, str) or exchange not in STOCK_MARKETS:
            raise ValueError("Stock example needs a supported exchange")
        currency, timezone_name = STOCK_MARKETS[exchange]
        pattern = CODE if exchange == "東証" else US_CODE
        if not isinstance(code, str) or not pattern.fullmatch(code):
            raise ValueError("Stock example code does not match its exchange")
        # Vendors use both BRK.B and BRK-B for the same US share class.
        identity_code = code if exchange == "東証" else code.replace("-", ".")
        if (exchange, identity_code) in seen:
            raise ValueError("Duplicate exchange and stock code")
        seen.add((exchange, identity_code))
        if pick.get("currency") != currency:
            raise ValueError("Stock currency does not match its exchange")
        if pick.get("price_kind") not in ("close", "intraday"):
            raise ValueError("Stock example must distinguish close and intraday prices")
        market_day = _date(pick.get("market_date"))
        previous_day = _date(pick.get("previous_market_date"))
        if previous_day >= market_day:
            raise ValueError("Previous close date must precede the stock market date")
        observed = _timestamp(pick.get("observed_at"))
        if observed > published:
            raise ValueError("Stock quote cannot postdate publication")
        market_zone = ZoneInfo(timezone_name)
        if observed.astimezone(market_zone).date() != market_day:
            raise ValueError("Stock market date must match its exchange-local observation date")
        normal_open, normal_close = STOCK_SESSIONS[exchange]
        opening = datetime.combine(market_day, normal_open, tzinfo=market_zone)
        closing = datetime.combine(market_day, normal_close, tzinfo=market_zone)
        session_fields = {"session_close_at", "session_source_url"}.intersection(pick)
        if len(session_fields) == 1:
            raise ValueError("Session close override needs both time and source")
        if session_fields:
            session_close = _timestamp(pick["session_close_at"])
            session_source = pick["session_source_url"]
            if not isinstance(session_source, str) or session_source not in source_urls:
                raise ValueError("Session close override requires a checked report source")
            if (session_close.astimezone(market_zone).date() != market_day
                    or not opening < session_close <= closing):
                raise ValueError("Session close must be on the market date within normal trading hours")
            if session_close > published:
                raise ValueError("Session close cannot postdate publication")
            closing = session_close
        if pick["price_kind"] == "close" and observed < closing:
            raise ValueError("Closing quote cannot precede its exchange session close")
        if not isinstance(pick.get("source_url"), str) or pick["source_url"] not in source_urls:
            raise ValueError("Stock price requires report source")
        previous, current = _amount(pick.get("previous_close")), _amount(pick.get("close"))
        if previous <= 0 or current <= 0:
            raise ValueError("Prices must be greater than zero")
        change = 100 * (current / previous - 1)
        if not math.isfinite(change):
            raise ValueError("Stock percentage must be finite")
        direction = pick.get("direction")
        expected = "上昇" if current > previous else "下落" if current < previous else "横ばい"
        if direction in ("急騰", "急落"):
            if abs(change) < 5 or (change > 0) != (direction == "急騰"):
                raise ValueError("Risers and fallers must move at least 5%, split-adjusted")
        elif direction != expected:
            raise ValueError("Stock direction must agree with its price change")
        if pick.get("corporate_action_checked") is not True:
            raise ValueError("Splits and reverse splits must be checked")
        _txt(pick.get("name"), "stock name", maximum=75)
        _txt(pick.get("adjustment_note"), "corporate-action method", maximum=230)
        _txt(pick.get("context"), "verified context", minimum=25, maximum=350)


def _coverage_label(report):
    return ("日米株の材料と値動き" if report.get("coverage") == "investor"
            else SLOTS[report["slot"]][1])


def load_market_bulletins(root, *, now=None):
    path = Path(root) / FILE
    if not path.is_file():
        return []
    reports = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(reports, list):
        raise ValueError("Market dispatch archive must be a list")
    present = now or datetime.now(ZoneInfo('Asia/Tokyo')).date()
    seen = set()
    previous = None
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError("Invalid market dispatch")
        if "coverage" in report and report["coverage"] != "investor":
            raise ValueError("Unknown market dispatch coverage mode")
        slug = report.get("slug")
        slot = report.get("slot")
        day = report.get("day")
        if (not isinstance(slug, str) or not KEY.fullmatch(slug)
                or slot not in SLOTS or slug != f"{day}-{slot}" or slug in seen):
            raise ValueError("Invalid or duplicate market dispatch identity")
        seen.add(slug)
        pub = _timestamp(report.get("published_at"))
        if pub.date().isoformat() != day or pub.date() > present:
            raise ValueError("Market dispatch date must match actual publication")
        if previous is not None and pub <= previous:
            raise ValueError("Market dispatches must be strictly chronological")
        previous = pub
        for name in ("title", "summary", "lead"):
            _txt(report.get(name), name, minimum=20 if name != "title" else 8)
        if len(report["title"]) > 100 or len(report["summary"]) > 230:
            raise ValueError("Market dispatch title or summary too long")
        srcs = report.get("sources")
        if not isinstance(srcs, list) or len(srcs) < 1:
            raise ValueError("Market dispatch needs checked public sources")
        source_urls = set()
        for src in srcs:
            if not isinstance(src, dict):
                raise ValueError("Invalid market source")
            _txt(src.get("title"), "source title", maximum=180)
            u = _url(src.get("url"))
            if u in source_urls:
                raise ValueError("Duplicate source")
            source_urls.add(u)
            published = _date(src.get("published_on"))
            checked = _date(src.get("checked_on"))
            if published > pub.date() or checked < published or checked > pub.date():
                raise ValueError("Source published or checked after market dispatch")
        sections = report.get("sections")
        if not isinstance(sections, list) or not 1 <= len(sections) <= 4:
            raise ValueError("Market dispatch requires concise analysis sections")
        for section in sections:
            _txt(section.get("heading") if isinstance(section, dict) else None, "section heading", maximum=95)
            paragraphs = section.get("paragraphs")
            if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 5:
                raise ValueError("Market dispatch needs paragraphs")
            for paragraph in paragraphs:
                _txt(paragraph, "paragraph", minimum=35, maximum=1500)
        figures = report.get("figures", [])
        if not isinstance(figures, list) or len(figures) not in (2, 3):
            raise ValueError("Published market dispatch requires two or three sourced graphics")
        figure_ids = set()
        for fig in figures:
            if not isinstance(fig, dict):
                raise ValueError("Invalid market dispatch figure")
            fid = fig.get("id")
            if not isinstance(fid, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fid) or fid in figure_ids:
                raise ValueError("Duplicate or invalid market dispatch figure")
            figure_ids.add(fid)
            if fig.get("kind") not in ("bars", "composition"):
                raise ValueError("Unsupported market chart type")
            for name, limit in (("title", 55), ("note", 250), ("unit", 10)):
                _txt(fig.get(name), name, maximum=limit)
            if fig.get("source_url") not in source_urls:
                raise ValueError("Chart must link its report's checked public source")
            evidence_time = _timestamp(fig.get("observed_at"))
            if evidence_time > pub:
                raise ValueError("Chart cannot cite market data after publication")
            values = fig.get("values")
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError("Chart requires two comparable sourced numbers")
            for value in values:
                _txt(value.get("label") if isinstance(value, dict) else None, "chart label", maximum=18)
                _amount(value.get("value"))
                if value.get("state") not in STATES:
                    raise ValueError("Chart must label actual, forecast or derived numbers")
            if fig["kind"] == "composition":
                total = _amount(fig.get("total"))
                if not total or abs(total - sum(v["value"] for v in values)) > 1e-6:
                    raise ValueError("Chart composition must add to verified total")
                _txt(fig.get("total_label"), "chart total label")
        # A compact, auditable situation map. Each claimed tailwind/headwind
        # must point to a source in the current issue and a real observation time.
        conditions = report.get("conditions", [])
        if not isinstance(conditions, list) or len(conditions) > 3:
            raise ValueError("Market conditions must be at most three")
        for condition in conditions:
            if not isinstance(condition, dict):
                raise ValueError("Invalid situation signal")
            _txt(condition.get("name"), "situation name", maximum=25)
            _txt(condition.get("meaning"), "situation interpretation", minimum=25, maximum=280)
            if condition.get("signal") not in ("追い風", "逆風", "中立", "未確認"):
                raise ValueError("Unlabelled market situation")
            if condition.get("source_url") not in source_urls:
                raise ValueError("Situation requires checked public source")
            if _timestamp(condition.get("observed_at")) > pub:
                raise ValueError("Situation may not use later information")
        picks = report.get("picks", [])
        if report.get("coverage") == "investor":
            _validate_investor_picks(picks, source_urls, pub)
        elif slot in ("am", "open", "noon"):
            if picks:
                raise ValueError("Broad market overview cannot be individual-stock picks")
        else:
            if not isinstance(picks, list) or len(picks) < 2:
                raise ValueError("Close report needs both Japanese risers and fallers")
            directions = set()
            unique = set()
            for pick in picks:
                if not isinstance(pick, dict):
                    raise ValueError("Invalid market stock pick")
                code = pick.get("code")
                if not isinstance(code, str) or not CODE.fullmatch(code) or code in unique:
                    raise ValueError("Only validated Japanese-listed security codes")
                unique.add(code)
                if pick.get("exchange") != "東証" or pick.get("direction") not in ("急騰", "急落"):
                    raise ValueError("Evening stocks must be Tokyo-listed gainers/losers")
                directions.add(pick["direction"])
                _txt(pick.get("name"), "stock name", maximum=75)
                if pick.get("source_url") not in source_urls:
                    raise ValueError("Stock price requires report source")
                price_time = _timestamp(pick.get("observed_at"))
                if price_time > pub:
                    raise ValueError("Stock quote cannot postdate publication")
                prev, current = _amount(pick.get("previous_close")), _amount(pick.get("close"))
                if prev <= 0 or current <= 0:
                    raise ValueError("Prices must be greater than zero")
                change = 100 * (current / prev - 1)
                if abs(change) < 5 or (change > 0) != (pick["direction"] == "急騰"):
                    raise ValueError("Risers and fallers must move at least 5%, split-adjusted")
                if pick.get("corporate_action_checked") is not True:
                    raise ValueError("Splits and reverse splits must be checked")
                _txt(pick.get("adjustment_note"), "corporate-action method", maximum=230)
                _txt(pick.get("context"), "verified context", minimum=25, maximum=350)
            if directions != {"急騰", "急落"}:
                raise ValueError("Evening dispatch requires both a gainer and a faller")
    return reports


def figure_url(report, figure, mobile=False):
    label = figure["id"] + ("-mobile" if mobile else "") + ".svg"
    return bulletin_url(report).lstrip("/") + "figures/" + label


def chart_html(report, fig):
    regular = render_market_svg(fig)
    compact = render_market_svg(fig, mobile=True)
    full_url = "/" + figure_url(report, fig) + "?v=" + sha256(regular.encode()).hexdigest()[:12]
    phone_url = "/" + figure_url(report, fig, mobile=True) + "?v=" + sha256(compact.encode()).hexdigest()[:12]
    src = next(src for src in report["sources"] if src["url"] == fig["source_url"])
    return (
        '<figure class="market-figure market-dispatch-figure">'
        '<picture>'
        f'<source media="(max-width:600px)" srcset="{escape(phone_url,quote=True)}" type="image/svg+xml">'
        f'<img src="{escape(full_url,quote=True)}" width="940" height="372" loading="lazy" decoding="async" alt="{escape(figure_description(fig),quote=True)}">'
        '</picture><figcaption>'
        f'<strong>{escape(fig["title"])}</strong>'
        f'<p>{escape(fig["note"])}</p>'
        f'<p class="market-figure-meta">観測時点：<time datetime="{fig["observed_at"]}">{escape(fig["observed_at"])}</time> ／ 単位：{escape(fig["unit"])}</p>'
        f'<a href="{escape(fig["source_url"],quote=True)}" target="_blank" rel="noopener noreferrer external">出典：{escape(src["title"])}</a>'
        '</figcaption></figure>'
    )


def _market_conditions(report):
    conditions = report.get("conditions", [])
    if not conditions:
        return ""
    cards = []
    for item in conditions:
        cards.append(
            '<article class="market-condition">'
            f'<h3>{escape(item["name"])}</h3><span>{escape(item["signal"])}</span>'
            f'<p>{escape(item["meaning"])}</p>'
            f'<a href="{escape(item["source_url"],quote=True)}" target="_blank" rel="noopener noreferrer external">'
            f'観測 {_timestamp(item["observed_at"]).strftime("%m/%d %H:%M")}・出典を確認</a>'
            '</article>'
        )
    return (
        '<section class="market-conditions"><h2>相場を動かす情勢</h2>'
        '<div class="market-conditions-grid">' + "".join(cards) + '</div></section>'
    )


def _picks(report):
    if not report.get("picks"):
        return ""
    rows = []
    investor = report.get("coverage") == "investor"
    for pick in report["picks"]:
        pct = (pick["close"] / pick["previous_close"] - 1) * 100
        positive = pct > 0
        modifier = ("market-stock-pick--up" if positive else "market-stock-pick--down"
                    if pct < 0 else "market-stock-pick--flat" if investor else "")
        exchange = pick["exchange"] if investor else "東証"
        if investor:
            currency = escape(pick["currency"])
            price_label = "終値" if pick["price_kind"] == "close" else "途中値"
            market_clock = "日本時間" if exchange == "東証" else "米国東部時間"
            observed = _timestamp(pick["observed_at"])
            price_html = (
                f'<p>比較元終値（{escape(pick["previous_market_date"])}）：'
                f'{pick["previous_close"]:,.2f} {currency} ／ '
                f'{price_label}：{pick["close"]:,.2f} {currency}</p>'
                f'<p>取引日：{escape(pick["market_date"])}（{market_clock}） ／ '
                f'観測：<time datetime="{escape(pick["observed_at"], quote=True)}">'
                f'{observed.strftime("%Y-%m-%d %H:%M")}</time> JST</p>'
            )
            if pick.get("session_close_at"):
                local_close = _timestamp(pick["session_close_at"]).astimezone(
                    ZoneInfo(STOCK_MARKETS[exchange][1]))
                price_html += (
                    '<p>取引終了：'
                    f'<time datetime="{escape(pick["session_close_at"], quote=True)}">'
                    f'{local_close.strftime("%Y-%m-%d %H:%M")}</time>（{market_clock}） ／ '
                    f'<a href="{escape(_url(pick["session_source_url"]), quote=True)}" '
                    'target="_blank" rel="noopener noreferrer external">取引時間の出典</a></p>'
                )
        else:
            price_html = (
                f'<p>前日終値：{pick["previous_close"]:,.2f}円 ／ 観測値：{pick["close"]:,.2f}円</p>'
                f'<p>観測：{escape(pick["observed_at"])}</p>'
            )
        rows.append(
            f'<article class="market-stock-pick {modifier}">'
            f'<span class="market-stock-direction">{escape(pick["direction"])}</span>'
            f'<h3>{escape(pick["name"])} <small>{escape(pick["code"])}・{escape(exchange)}</small></h3>'
            f'<p class="market-stock-change">{pct:+.2f}%</p>'
            + price_html +
            f'<p>{escape(pick["context"])}</p>'
            f'<p>分割等の確認・補正：{escape(pick["adjustment_note"])}</p>'
            f'<a href="{escape(pick["source_url"],quote=True)}" target="_blank" rel="noopener noreferrer external">価格・変動の出典を見る</a>'
            '</article>'
        )
    heading = "注目銘柄の材料と値動き" if investor else "急騰と急落：日本株の注目例"
    return f'<section class="market-stock-picks"><h2>{heading}</h2><p>網羅的な騰落率ランキングではなく、一次資料等で確認できた個別例です。</p><div class="market-stock-grid">' + "".join(rows) + '</div></section>'


def render_market_bulletin(report, layout, outlooks=()):
    slot_name = _coverage_label(report)
    # Never render a later forecast/update as though it existed at publication.
    published_at = _timestamp(report["published_at"])
    slot_time = published_at.strftime("%H:%M")
    latest_forecast = view_models(outlooks, before=published_at) if outlooks else []
    if latest_forecast:
        current = latest_forecast[0]
        forecast = current["forecast"]
        forecast_section = (
            '<section class="market-briefing-outlook market-reader-focus" id="daily-forecasts">'
            '<h2>この日の予想</h2>'
            f'<p class="market-reader-prediction">{escape(forecast_reader_statement(forecast))}</p>'
            f'<p class="market-briefing-forecast-meta">結果日：'
            f'<time datetime="{forecast["reviewed_on"]}">{forecast["reviewed_on"]}</time></p>'
            '<a href="/serials/market-lab/outlooks/">理由と結果を読む →</a>'
            '</section>'
        )
    else:
        forecast_section = (
            '<p class="market-briefing-more"><a href="/serials/market-lab/outlooks/">'
            '市場の予想と結果を読む →</a></p>'
        )
    parts = []
    for i, section in enumerate(report["sections"]):
        parts.append(f'<section class="serial-prose-section"><h2>{escape(section["heading"])}</h2>')
        parts.extend(f'<p>{escape(text)}</p>' for text in section["paragraphs"])
        parts.append("</section>")
        parts.extend(chart_html(report, fig) for ix, fig in enumerate(report["figures"]) if ix == i)
    parts.extend(chart_html(report, fig) for ix, fig in enumerate(report["figures"]) if ix >= len(report["sections"]))
    source_html = "".join(
        f'<li><a href="{escape(src["url"],quote=True)}" rel="noopener noreferrer external" target="_blank">{escape(src["title"])}</a>'
        f'（資料日 {src["published_on"]} ／ 確認日 {src["checked_on"]}）</li>'
        for src in report["sources"]
    )
    main = (
        '<div class="container serials-page serial-article serial-article--analysis market-dispatch-page">'
        f'<nav class="serial-breadcrumb"><a href="/">トップ</a> / <a href="{MARKET_URL}">未来相場観測室</a> / '
        '<a href="/serials/market-lab/briefings/">日次市況</a></nav>'
        '<header class="serial-article-head">'
        f'<span class="serial-type">実データの市況解説 / {slot_time} {slot_name}</span>'
        f'<h1>{escape(report["title"])}</h1><p class="serial-article-dek">{escape(report["summary"])}</p>'
        f'<p class="serial-pub">実公開：<time datetime="{report["published_at"]}">{escape(report["published_at"])}</time></p>'
        '<p class="serial-article-warning">架空ニュースではありません。売買助言・推奨ではありません。観測値は表示時点のリアルタイム価格ではありません。</p>'
        + render_market_share(bulletin_url(report), report["title"])
        + '</header>' + render_market_freshness(report, include_publication=False)
        + render_market_accuracy(outlooks, before=published_at) + forecast_section
        + render_market_numbers(report["figures"], sources=report["sources"])
        + '<div class="serial-article-body">'
        f'<p class="market-dispatch-lead">{escape(report["lead"])}</p>'
        + _market_conditions(report) + "".join(parts)
        + _picks(report)
        + '<section class="serial-references"><h2>確認した公開資料</h2><ol>' + source_html + '</ol></section>'
        + render_reader_comments('market-briefing-' + report['slug'],
             heading='この市況へのコメント', placeholder='今日の相場についてひとこと…')
        + '<p><a class="serial-back-link" href="/serials/market-lab/briefings/">日次市況一覧へ</a></p>'
        '</div></div>'
    )
    return layout(report["title"], report["summary"], bulletin_url(report),
                  main, active="serials", kind="market_bulletin",
                  serial={"slug": "market-lab", "kind": "analysis",
                          "episode_title": report["title"], "episode": report["slug"],
                          "published_at": report["published_at"]})


def render_market_bulletins_index(reports, *, limit=12):
    """Keep the front page compact, but never truncate the full archive."""
    def published_time(item):
        raw = item.get("published_at")
        if raw:
            stamp = _timestamp(raw)
            value, label = stamp.isoformat(), stamp.strftime("%Y-%m-%d %H:%M")
        else:
            value = label = item["day"]
        return f'<time datetime="{escape(value, quote=True)}">{escape(label)}</time>'

    cards = "".join(
        f'<li><a href="{bulletin_url(item)}">'
        f'<span class="market-paper-story-date">{published_time(item)}'
        f' ／ {escape(_coverage_label(item))}</span>'
        f'<strong>{escape(item["title"])}</strong>'
        f'<span class="market-paper-story-summary">{escape(item["summary"])}</span>'
        '</a></li>'
        for item in reversed(reports if limit is None else reports[-limit:])
    )
    return (
        '<section class="market-desk market-paper-bulletins" id="market-desk">'
        '<div class="market-paper-heading"><h2>日々の市況</h2>'
        '<a href="/feeds/market-bulletins.xml" type="application/rss+xml">市況RSS</a></div>'
        + ('<ol class="market-paper-story-list">' + cards + '</ol>'
           if cards else
           '<p class="market-paper-section-intro">現在、掲載中の市況記事はありません。'
           '<a href="/serials/market-lab/">最新の企業分析を読む</a></p>')
        + ('<p class="market-paper-see-all"><a href="/serials/market-lab/briefings/">市況記事をすべて読む →</a></p>'
           if cards and limit is not None else '')
        + '</section>'
    )


def render_market_bulletins_archive(reports, layout):
    content = (
        '<div class="container serials-page market-desk-archive">'
        f'<nav class="serial-breadcrumb"><a href="/">トップ</a> / <a href="{MARKET_URL}">未来相場観測室</a></nav>'
        '<header class="serials-intro"><h1>日次市況</h1><p>日本株・米国株の値動きと材料を、確認した公開資料から読む。</p>'
        + render_market_share(MARKET_URL + "briefings/", "日次市況") + '</header>'
        + render_market_bulletins_index(reports, limit=None)
        + '</div>'
    )
    return layout("日次市況 | 未来相場観測室",
                  "日本と米国の指数・個別株について、値動き、材料、次の注目点を出典付きで記録。",
                  MARKET_URL + "briefings/", content, active="serials",
                  serial={"slug":"market-lab","kind":"analysis","title":"未来相場観測室"},
                  feed_url=FEED_URL)


def market_bulletin_feed_xml(reports):
    root = ET.Element("rss", version="2.0")
    chan = ET.SubElement(root, "channel")
    for tag, val in (
        ("title", "未来相場観測室｜日次市況"),
        ("link", SITE + MARKET_URL + "briefings/"),
        ("description", "現実の公開資料に基づく日米の指数・個別株の材料と値動き。売買助言ではありません。"),
        ("language", "ja"),
    ):
        ET.SubElement(chan, tag).text = val
    for item in reversed(reports[-50:]):
        e = ET.SubElement(chan, "item")
        for tag, val in (
            ("title", item["title"]),
            ("link", SITE + bulletin_url(item)),
            ("guid", SITE + bulletin_url(item)),
            ("description", item["summary"]),
            ("pubDate", format_datetime(_timestamp(item["published_at"]))),
        ):
            ET.SubElement(e, tag).text = val
    return ET.tostring(root, encoding="unicode")


def market_bulletin_routes(reports):
    current = max((x["day"] for x in reports), default=None)
    return [(MARKET_URL + "briefings/", current)] + [
        (bulletin_url(item), item["published_at"]) for item in reports
    ]


def build_market_bulletins(reports, layout, write, *, outlooks=()):
    write(MARKET_URL.lstrip("/") + "briefings/index.html",
          render_market_bulletins_archive(reports, layout))
    write(FEED_URL.lstrip("/"), market_bulletin_feed_xml(reports))
    for item in reports:
        write(bulletin_url(item).lstrip("/") + "index.html",
              render_market_bulletin(item, layout, outlooks=outlooks))
        for fig in item["figures"]:
            for small in (False, True):
                write(figure_url(item, fig, mobile=small),
                      render_market_svg(fig, mobile=small))
