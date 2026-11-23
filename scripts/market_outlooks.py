"""Append-only daily market forecasts, snapshots, and evidence-linked stance changes.

This ledger is separate from the immutable long-horizon serial forecasts.
It never fetches prices. Outcomes are computed only from explicitly recorded
official evidence after the fixed judgement date. All displayed dates are real
JST dates, not fictional newspaper dates.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import math
import re

if __package__:
    from .market_sharing import render_market_share
    from .forecast_ledger import reader_reason_sentences, forecast_horizon
else:
    from market_sharing import render_market_share
    from forecast_ledger import reader_reason_sentences, forecast_horizon

PATH = Path("content/market_outlooks.json")
JST = timedelta(hours=9)
STANCES = ("強気", "中立", "弱気")
TRENDS = ("暫定的に整合", "暫定的に逆行", "判断材料不足")
DIRECTIONS = ("上昇", "下落", "横ばい")
BASE_URL = "/serials/market-lab/outlooks/"
JP_CODE = r"(?:[0-9]{4}|[0-9]{3}[a-z])"
US_CODE = r"[a-z]{1,5}(?:-[a-z])?"
FORECAST_ID = re.compile(r"20\d{2}-\d{2}-\d{2}-(?P<instrument>topix|nikkei225|sp500|tse-"
                         + JP_CODE + r"|(?:nyse|nasdaq)-" + US_CODE + r")\Z")
INDEX_SUBJECTS = {"topix": "TOPIX", "nikkei225": "日経平均株価", "sp500": "S&P 500"}
EXCHANGE_LABELS = {"tse": "東証", "nyse": "NYSE", "nasdaq": "NASDAQ"}
REVIEW_ID = re.compile(r"20\d{2}-\d{2}-\d{2}-(?:am|open|noon|close)-[a-z0-9-]+\Z")


def _timestamp(s):
    if not isinstance(s, str):
        raise ValueError("A daily outlook needs a real publication timestamp")
    try:
        x = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError("Invalid daily outlook timestamp") from exc
    if x.utcoffset() != JST or x.isoformat(timespec="seconds") != s:
        raise ValueError("Market outlook timestamps must use +09:00 and seconds")
    return x


def _day(s):
    if not isinstance(s, str):
        raise ValueError("Market outlook requires canonical YYYY-MM-DD")
    try:
        x = date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError("Invalid market outlook date") from exc
    if x.isoformat() != s:
        raise ValueError("Noncanonical market outlook date")
    return x


def _text(value, name, minimum=1, maximum=1000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError("Market outlook requires " + name)
    return value


def _https(url):
    s = urlsplit(_text(url, "HTTPS evidence URL"))
    if s.scheme != "https" or not s.hostname or s.username or s.password:
        raise ValueError("Market outlook evidence must be HTTPS")
    return url


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Market outlook prices must be positive finite numbers")
    return value


def _source(source, *, asof):
    if not isinstance(source, dict):
        raise ValueError("Market outlook needs source record")
    _text(source.get("title"), "source title", maximum=180)
    _https(source.get("url"))
    published = _day(source.get("published_on"))
    checked = _day(source.get("checked_on"))
    if published > asof or checked > asof or checked < published:
        raise ValueError("Market outlook evidence cannot postdate publication")
    return source["url"]


def forecast_instrument_id(forecast):
    identity = forecast.get("id")
    match = FORECAST_ID.fullmatch(identity) if isinstance(identity, str) else None
    if not match:
        raise ValueError("Unsafe market forecast identity")
    return match["instrument"]


def _instrument_market(key):
    return "JP" if key in ("topix", "nikkei225") or key.startswith("tse-") else "US"


def _instrument_unit(key):
    if key in ("topix", "sp500"):
        return "ポイント"
    return "JPY" if _instrument_market(key) == "JP" else "USD"


def forecast_display_unit(forecast):
    unit = _instrument_unit(forecast_instrument_id(forecast))
    return {"JPY": "円", "USD": "米ドル"}.get(unit, unit)


def _subject_matches(key, subject):
    if key in INDEX_SUBJECTS:
        return subject == INDEX_SUBJECTS[key]
    exchange, ticker = key.split("-", 1)
    suffix = f"（{ticker.upper().replace('-', '.')}・{EXCHANGE_LABELS[exchange]}）"
    return isinstance(subject, str) and len(subject) <= 110 and subject.endswith(suffix) and bool(subject[:-len(suffix)].strip())


def _market_zone(key):
    return ZoneInfo("Asia/Tokyo" if _instrument_market(key) == "JP" else "America/New_York")


def market_forecast_horizon(forecast):
    # The result date is the trading venue's date, even while Japan is a day ahead.
    started = _timestamp(forecast["at"])
    if forecast.get("target_session"):
        started = started.astimezone(_market_zone(forecast_instrument_id(forecast))).date()
    return forecast_horizon(started, forecast["reviewed_on"])


def _hours(key, market_day):
    japan = _instrument_market(key) == "JP"
    zone = _market_zone(key)
    return (datetime.combine(market_day, time(9) if japan else time(9, 30), tzinfo=zone),
            datetime.combine(market_day, time(15, 30) if japan else time(16), tzinfo=zone))


def _scheduled_close(key, market_day, session, cited):
    if not isinstance(session, dict) or market_day.weekday() >= 5:
        raise ValueError("Expected a sourced cash-market trading session")
    url = _https(session.get("source_url"))
    host = urlsplit(url).hostname
    owners = ("jpx.co.jp",) if _instrument_market(key) == "JP" else ("nyse.com", "nasdaq.com", "nasdaqtrader.com")
    if url not in cited or not any(host == owner or host.endswith("." + owner) for owner in owners):
        raise ValueError("Session must cite the relevant exchange calendar")
    close = _timestamp(session.get("close_at"))
    opening, regular_close = _hours(key, market_day)
    if not opening < close <= regular_close:
        raise ValueError("Session close must match the local exchange date and hours")
    return close


def _quote(key, quote, source, market_day, published, *, price_kind, close_at=None):
    if (quote.get("instrument_id") != key or source.get("instrument_id") != key or
            quote.get("unit") != _instrument_unit(key) or quote.get("price_kind") not in price_kind):
        raise ValueError("Price/source instrument, unit or quote type mismatch")
    # A provider's separately recorded quote metadata must agree when supplied.
    if (source.get("price_kind", quote["price_kind"]) != quote["price_kind"] or
            source.get("unit", quote["unit"]) != quote["unit"] or
            source.get("market_date", market_day.isoformat()) != market_day.isoformat()):
        raise ValueError("Cited price record differs from this quote")
    observed = _timestamp(quote.get("observed_at"))
    opening, regular_close = _hours(key, market_day)
    closing = close_at or regular_close
    if (market_day.weekday() >= 5 or observed > published or observed < opening or
            observed.astimezone(opening.tzinfo).date() != market_day):
        raise ValueError("Price observation must match its local trading day before publication")
    if quote["price_kind"] == "close" and observed < closing:
        raise ValueError("Official close not yet observed for this session")
    if (quote["price_kind"] == "close" and observed > closing and
            (source.get("price_kind") != "close" or source.get("market_date") != market_day.isoformat())):
        raise ValueError("Post-session observation needs a dated official closing-price source")
    if quote["price_kind"] == "intraday" and observed > closing:
        raise ValueError("Intraday quote must be during its session")
    return observed


def forecast_result_due(forecast, asof):
    """The target regular session must have ended, regardless of the reader's timezone."""
    if not isinstance(asof, datetime) or asof.utcoffset() is None:
        raise ValueError("A market result needs a timezone-aware comparison time")
    if forecast_instrument_id(forecast) == "topix":
        close = _hours("topix", _day(forecast["reviewed_on"]))[1]
    else:
        close = _timestamp(forecast["target_session"]["close_at"])
    return asof >= close


def _validated_result(fc, event):
    result, source = event["result"], event["source"]
    at, target = _timestamp(event["at"]), _day(fc["reviewed_on"])
    if not forecast_result_due(fc, at):
        raise ValueError("No verified result before fixed judgement date and close")
    if not isinstance(result, dict):
        raise ValueError("Market result must contain evidence")
    close = _number(result.get("close"))
    if result.get("observed_on") != fc["reviewed_on"]:
        raise ValueError("Market outcome must use its original evaluation day")
    if _https(result.get("source_url")) != source["url"]:
        raise ValueError("Market outcome must cite the checked source")
    key = forecast_instrument_id(fc)
    if key == "topix":
        for record in (result, source):
            if (record.get("instrument_id", key) != key or record.get("unit", "ポイント") != "ポイント"
                    or record.get("price_kind", "close") != "close" or record.get("market_date", fc["reviewed_on"]) != fc["reviewed_on"]):
                raise ValueError("Cited result differs from the original TOPIX forecast")
    else:
        _quote(key, result, source, target, at, price_kind=("close",),
               close_at=_timestamp(fc["target_session"]["close_at"]))
        if key not in INDEX_SUBJECTS:
            if result.get("corporate_action_checked") is not True:
                raise ValueError("Stock outcome needs a comparable share basis")
            _text(result.get("adjustment_note"), "stock price adjustment note", maximum=350)
    if result.get("outcome") != _forecast_result(fc, close):
        raise ValueError("Market result inconsistent with frozen criterion")
    _text(result.get("explanation"), "result explanation", minimum=35)
    return result


def _observed_direction(baseline, close):
    # Compare the recorded decimal prices directly. Exactly ±0.5% is flat.
    start = Decimal(str(baseline))
    movement = (Decimal(str(close)) - start) * 200
    if movement > start:
        return "上昇"
    if movement < -start:
        return "下落"
    return "横ばい"


def _change_label(baseline, close):
    start = Decimal(str(baseline))
    change = (Decimal(str(close)) - start) * 100 / start
    rounded = change.quantize(Decimal("0.01"))
    # Rounding must not make a directional verdict look exactly flat.
    if abs(rounded) == Decimal("0.5") and abs(change) > abs(rounded):
        return "＋0.5%超" if change > 0 else "−0.5%未満"
    if not rounded:
        return "0.00%" if not change else "変化幅0.01%未満"
    return ("＋" if change > 0 else "−") + f'{abs(rounded):.2f}%'


def _forecast_result(forecast, observed):
    actual = _observed_direction(forecast["baseline"]["value"], observed)
    return "的中" if forecast["direction"] == actual else "外れ"


def load_market_outlooks(root, *, today=None):
    """Load evidence-checked chronological events; never infer a hit from trend."""
    f = Path(root) / PATH
    if not f.is_file():
        return []
    events = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise ValueError("Market outlook history must be a list")
    clock = datetime.now(ZoneInfo("Asia/Tokyo")) if today is None else None
    now = clock.date() if clock else today
    forecasts = {}
    latest_stance = {}
    verified = set()
    seen = set()
    last_at = None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid market outlook entry")
        kind, eid = event.get("type"), event.get("id")
        if not isinstance(eid, str) or eid in seen:
            raise ValueError("Duplicate or missing market outlook identity")
        seen.add(eid)
        at = _timestamp(event.get("at"))
        if at.date() > now or (clock and at > clock) or (last_at is not None and at < last_at):
            raise ValueError("Market outlook events must be chronological and not future dated")
        last_at = at
        if kind == "forecast":
            if not FORECAST_ID.fullmatch(eid) or eid[:10] != at.date().isoformat() or eid in forecasts:
                raise ValueError("Daily forecast must have one real JST-date identity")
            target_day = _day(event.get("reviewed_on"))
            key = forecast_instrument_id(event)
            if key == "topix" and target_day <= at.date():
                raise ValueError("Daily forecast judgement must be after publication")
            if (event.get("instrument_id", key) != key or not _subject_matches(key, event.get("subject"))
                    or event.get("direction") not in DIRECTIONS):
                raise ValueError("Daily forecast must identify its original instrument and direction")
            if event.get("stance") not in STANCES:
                raise ValueError("Daily forecast stance must be clear")
            for k, n in (("statement", 25), ("method", 35),
                         ("reason", 35), ("invalidated_if", 25)):
                _text(event.get(k), k, minimum=n)
            if "0.5" not in event["method"]:
                raise ValueError("Daily forecast requires the fixed 0.5 percent threshold")
            scenarios = event.get("scenarios")
            if not isinstance(scenarios, dict) or set(scenarios) != set(STANCES):
                raise ValueError("Daily forecast needs three scenarios")
            for val in scenarios.values():
                _text(val, "scenario", minimum=18)
            sources = event.get("sources")
            if not isinstance(sources, list) or not sources:
                raise ValueError("Daily forecast needs public evidence")
            urls = {_source(src, asof=at.date()) for src in sources}
            if len(urls) != len(sources):
                raise ValueError("Duplicate market outlook source")
            baseline = event.get("baseline")
            if not isinstance(baseline, dict):
                raise ValueError("Daily forecast requires a verified baseline")
            baseline_day = _day(baseline.get("date"))
            if baseline_day > at.date():
                raise ValueError("Baseline cannot be from the future")
            _number(baseline.get("value"))
            if baseline.get("unit") != _instrument_unit(key) or baseline.get("source_url") not in urls:
                raise ValueError("Baseline needs the correct units and cited public source")
            quote_source = next(src for src in sources if src["url"] == baseline["source_url"])
            if key == "topix":
                if any(record.get("instrument_id", key) != key for record in (baseline, quote_source)):
                    raise ValueError("Cited baseline differs from the original TOPIX forecast")
            else:
                target_close = _scheduled_close(key, target_day, event.get("target_session"), urls)
                if at >= target_close or baseline_day >= target_day:
                    raise ValueError("Target session must close after publication and follow the baseline")
                prior_session = baseline.get("session")
                prior_close = _scheduled_close(key, baseline_day, prior_session, urls) if prior_session is not None else None
                _quote(key, baseline, quote_source, baseline_day, at, price_kind=("close",), close_at=prior_close)
            _text(baseline.get("method"), "baseline method", minimum=20)
            forecasts[eid] = event
            latest_stance[eid] = event["stance"]
        elif kind == "review":
            if not REVIEW_ID.fullmatch(eid):
                raise ValueError("Market review identity must include real day and slot")
            if eid[:10] != at.date().isoformat():
                raise ValueError("Market review ID and date differ")
            fid = event.get("forecast_id")
            if fid not in forecasts or fid in verified:
                raise ValueError("Market review references absent or already verified forecast")
            fc = forecasts[fid]
            key = forecast_instrument_id(fc)
            if event.get("instrument_id", key) != key:
                raise ValueError("Review instrument differs from the original forecast")
            tail = eid.split("-", 4)[4]
            if key != "topix" or re.match(r"20\d{2}-", tail):
                if tail != fid and not re.fullmatch(re.escape(fid) + r"-[0-9]{2}", tail):
                    raise ValueError("Review identity must include the original forecast identity")
            if at <= _timestamp(fc["at"]):
                raise ValueError("Market review must follow its original forecast")
            if event.get("stance") not in STANCES or event.get("trend") not in TRENDS:
                raise ValueError("Market review needs stance and provisional trend")
            _text(event.get("reason"), "review reason", minimum=35)
            source = event.get("source")
            _source(source, asof=at.date())
            if event["stance"] != latest_stance[fid]:
                _text(event.get("change_reason"), "stance-change reason", minimum=35)
            if event.get("observed_value") is not None:
                _number(event["observed_value"])
                market_day = _day(event.get("observed_on"))
                if market_day > at.date():
                    raise ValueError("Observed value cannot be future-dated")
                if key != "topix":
                    closing = (_timestamp(fc["target_session"]["close_at"]) if market_day == _day(fc["reviewed_on"])
                               else None)
                    observed = _quote(key, event, source, market_day, at, price_kind=("close", "intraday"), close_at=closing)
                    if observed <= _timestamp(fc["at"]):
                        raise ValueError("Interim price must follow the original forecast")
            outcome = event.get("result")
            if outcome is not None:
                _validated_result(fc, event)
                verified.add(fid)
            latest_stance[fid] = event["stance"]
        else:
            raise ValueError("Unknown market outlook event type")
    return events


def view_models(events, *, before=None):
    """Keep each forecast's visible reviews and the recorded view time together."""
    visible = [entry for entry in events
               if before is None or _timestamp(entry["at"]) <= before]
    asof = before if before is not None else max(
        (_timestamp(entry["at"]) for entry in visible), default=None)
    forecasts = {}
    for entry in visible:
        if entry["type"] == "forecast":
            forecasts[entry["id"]] = {"forecast": entry, "updates": [], "asof": asof}
        elif entry["forecast_id"] in forecasts:
            forecasts[entry["forecast_id"]]["updates"].append(entry)
    return sorted(forecasts.values(), key=lambda f: f["forecast"]["at"], reverse=True)


def _event_day(at):
    return _timestamp(at).strftime("%Y年%m月%d日 %H:%M")


def _confirmed_result(row):
    """Only an evidenced final verdict counts; interim sentiment never does."""
    fc = row["forecast"]
    latest = row["updates"][-1] if row["updates"] else None
    result = latest.get("result") if latest else None
    if not isinstance(result, dict) or result.get("outcome") not in ("的中", "外れ"):
        return None
    try:
        return _validated_result(fc, latest)
    except (KeyError, ValueError, TypeError):
        return None


def _status(row):
    result = _confirmed_result(row)
    if result:
        return "判定済み：" + result["outcome"]
    latest = row["updates"][-1] if row["updates"] else None
    outcome = latest.get("result") if latest else None
    if isinstance(outcome, dict) and outcome.get("outcome") in ("判定不能", "保留"):
        return "判定保留"
    if forecast_result_due(row["forecast"], row["asof"]):
        return "結果日到来・確認待ち"
    return "結果待ち"


def _forecast_anchor(forecast):
    identity = forecast.get("id")
    if not isinstance(identity, str) or not FORECAST_ID.fullmatch(identity):
        raise ValueError("Unsafe market forecast anchor")
    _day(identity[:10])
    return "forecast-" + identity


def market_forecast_url(forecast):
    return BASE_URL + "#" + _forecast_anchor(forecast)


def forecast_reader_statement(forecast):
    """Display the original direction and target day without its test machinery."""
    target = _day(forecast["reviewed_on"])
    year = f'{target.year}年' if target.year != _timestamp(forecast["at"]).year else ''
    local = "（米国時間）" if _instrument_market(forecast_instrument_id(forecast)) == "US" else ""
    return f'{year}{target.month}月{target.day}日{local}の{forecast["subject"]}は{forecast["direction"]}を予想。'


def forecast_reader_reason(forecast):
    """Keep up to two complete existing market-material sentences, never new claims."""
    return reader_reason_sentences(forecast["reason"])


def pending_market_forecasts(events, *, before=None):
    """The nearest outstanding target per instrument, with the newest view on tied dates."""
    asof = before or datetime.now(ZoneInfo("Asia/Tokyo"))
    pending = {}
    for row in sorted(view_models(events, before=asof), key=lambda row: row["forecast"]["reviewed_on"]):
        fc = row["forecast"]
        if not forecast_result_due(fc, asof) and _confirmed_result(row) is None:
            pending.setdefault(forecast_instrument_id(fc), row)
    return list(pending.values())


def forecast_market_scope(forecast):
    instrument = forecast_instrument_id(forecast)
    if instrument == "topix":
        return "日本株全体"
    if instrument == "nikkei225":
        return "日本の主要225銘柄"
    if instrument == "sp500":
        return "米国の大型株"
    return "日本の個別株" if instrument.startswith("tse-") else "米国の個別株"


def render_market_accuracy(events, *, before=None):
    groups = {}
    for row in view_models(events, before=before):
        fc = row["forecast"]
        if market_forecast_horizon(fc) == "短期":
            groups.setdefault(forecast_instrument_id(fc), []).append(row)
    scores = [_instrument_score(rows, before) for rows in groups.values()] or [_instrument_score([], before)]
    older = ('<details class="market-reader-past"><summary>ほかの対象の成績</summary>'
             + ''.join(scores[3:]) + '</details>') if len(scores) > 3 else ''
    return (''.join(scores[:3]) + older
            + '<p class="market-score-basis">予想時に記録した基準終値と、判定日の終値を比較します。'
              '対象は公表から7日以内に判定する予想です。実際の方向が予想と合えば的中です。</p>'
              '<ul class="market-score-ranges" aria-label="終値の変化率と判定">'
              '<li>上昇 <b>＋0.5%超</b></li><li>横ばい <b>±0.5%以内</b></li><li>下落 <b>−0.5%未満</b></li></ul>')


def _instrument_score(rows, before):
    settled = [(row["forecast"], result) for row in rows if (result := _confirmed_result(row)) is not None]
    hits = sum(result["outcome"] == "的中" for _, result in settled)
    total = len(settled)
    pending = len(rows) - total
    scope = escape(rows[0]["forecast"]["subject"]) + 'の短期予想' if rows else '短期相場予想'
    percent = (f'{hits * 100 / total:.1f}'.rstrip('0').rstrip('.') + '%') if total else '—'
    count = f'判定済み{total}件のうち{hits}件が的中' if total else '確定0件'
    bar = (f'<meter class="market-score-meter" min="0" max="{total}" value="{hits}" aria-hidden="true"></meter>'
           if total else '')
    counts = ([f'外れ{total-hits}件'] if total else []) + ([f'未確定{pending}件は集計外'] if pending else [])
    when = ''
    if before is not None:
        asof = before.astimezone(ZoneInfo("Asia/Tokyo"))
        when = (f'<time datetime="{asof.isoformat(timespec="seconds")}">'
                f'{asof.month}月{asof.day}日 {asof:%H:%M} JST 掲載時点</time>')
    example = ''
    if settled:
        fc, result = max(settled, key=lambda pair: (pair[0]["reviewed_on"], pair[0]["at"]))
        day = _day(fc["reviewed_on"])
        move = _change_label(fc["baseline"]["value"], result["close"])
        actual = _observed_direction(fc["baseline"]["value"], result["close"])
        example = (f'<a class="market-score-example" href="{market_forecast_url(fc)}"><strong>{escape(fc["subject"])}の直近の判定</strong> '
                   f'{day.month}/{day.day}判定　{escape(fc["direction"])}予想 → {actual}（{move}）で{result["outcome"]}</a>')
    return ('<div class="market-reader-accuracy" aria-label="短期相場予想の的中率">'
            + example + f'<div class="market-score-heading"><b>{scope}の的中率</b>{when}</div>'
            f'<div class="market-score-value"><strong>{percent}</strong><span class="market-reader-count">{count}</span></div>'
            + bar + (f'<small>{"　／　".join(counts)}</small>' if counts else '')
            + '</div>')


def render_market_forecast_assessment(row):
    """Distinguish the original rationale, latest recorded assessment and verdict."""
    fc = row["forecast"]
    result = _confirmed_result(row)
    reason = forecast_reader_reason(fc)
    reason_html = f'<p class="market-reader-reason"><strong>当初の理由</strong>{escape(reason)}</p>' if reason else ''
    latest = row["updates"][-1] if row["updates"] else None
    assessment = '<p class="market-reader-date">この予想の更新評価はまだありません。</p>'
    if latest:
        source = latest["source"]
        date_label = '結果確認：' if result else '最新の評価：'
        reason_label = '判定の理由' if result else '評価の理由'
        source_label = '結果の出典：' if result else '評価の出典：'
        provisional = (f' ／ {escape(latest["stance"])}（{escape(latest["trend"])}）'
                       if not result else '')
        assessment = (
            f'<p class="market-reader-date">{date_label}'
            f'<time datetime="{escape(latest["at"], quote=True)}">{_event_day(latest["at"])}</time> JST'
            f'{provisional}</p>'
            f'<p class="market-reader-reason"><strong>{reason_label}</strong>{escape(latest["reason"])}</p>'
            '<p class="market-reader-sources">'
            f'<a href="{escape(_https(source["url"]), quote=True)}" target="_blank" '
            f'rel="noopener noreferrer external">{source_label}{escape(source["title"])}</a></p>'
        )
    status = _status(row)
    outcome = 'hit' if result and result["outcome"] == '的中' else 'miss' if result else 'pending'
    base = fc["baseline"]
    base_day = _day(base["date"])
    basis = (f'<span>基準終値 <time datetime="{base_day}">{base_day.month}/{base_day.day}</time> '
             f'{base["value"]:,.2f}{escape(forecast_display_unit(fc))}</span>')
    confirmed = (f'<span>確認済み終値 {result["close"]:,.2f}{escape(forecast_display_unit(fc))}（{_change_label(base["value"], result["close"])}）</span>'
                 if result else '')
    sources = ''.join(
        f'<a href="{escape(_https(source["url"]),quote=True)}" title="{escape(source["title"],quote=True)}" '
        f'target="_blank" rel="noopener noreferrer external">{escape(source["title"])}</a> '
        for source in fc["sources"]
    )
    asof = row["asof"].astimezone(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    actual = ''
    if result:
        direction = _observed_direction(fc["baseline"]["value"], result["close"])
        actual = f'<span class="market-direction-pair">予想 <b>{escape(fc["direction"])}</b> → 実際 <b>{direction}</b></span>'
    return (f'<div class="market-reader-result" data-outcome="{outcome}"><strong>{escape(status)}</strong>'
            + actual + f'<span>結果日：<time datetime="{escape(fc["reviewed_on"], quote=True)}">{escape(fc["reviewed_on"])}</time></span>'
            + basis + confirmed + '</div>' + reason_html
            + '<details class="market-reader-evidence"><summary>経過と出典</summary>'
            + assessment + f'<p class="market-reader-sources">当初の出典 {sources}</p>'
            + f'<p class="market-reader-date">集計時点：<time datetime="{asof}">{_event_day(asof)}</time> JST</p></details>')


def _reader_forecast_card(row):
    fc = row["forecast"]
    return (f'<article class="market-reader-card" id="{_forecast_anchor(fc)}">'
            f'<p class="market-reader-date"><span class="market-reader-horizon">'
            f'{market_forecast_horizon(fc)}</span> '
            f'<time datetime="{escape(fc["at"],quote=True)}">'
            f'{_event_day(fc["at"])}</time>の当初の予想</p>'
            f'<h3 class="market-reader-direction">{escape(forecast_reader_statement(fc))}</h3>'
            + render_market_forecast_assessment(row) + '</article>')


def render_market_outlook_board(events, *, limit=3, before=None, link_to_archive=True):
    if type(limit) is not int or limit < 0:
        raise ValueError("Forecast display limit must be nonnegative")
    reports = view_models(events, before=before)
    visible = min(limit, 3) if limit else 3
    cards = [_reader_forecast_card(row) for row in reports]
    recent = ('<div class="market-reader-cards">' + ''.join(cards[:visible]) + '</div>') if cards else (
        '<p class="market-desk-empty">新しい相場予想はまだありません。'
        '<a href="/serials/market-lab/">企業分析を読む</a></p>')
    past = ('<details class="market-reader-past"><summary>過去の予想</summary>'
            '<div class="market-reader-cards">' + ''.join(cards[visible:]) + '</div></details>') if cards[visible:] else ''
    archive = f'<p><a class="serial-feed-link" href="{BASE_URL}">相場の予想をすべて見る</a></p>' if link_to_archive else ''
    return ('<section class="daily-forecast-board" id="daily-forecasts" aria-labelledby="daily-forecasts-title">'
            '<div class="market-paper-heading"><h2 id="daily-forecasts-title">相場の予想と結果</h2></div>'
            + render_market_accuracy(events, before=before) + recent + past
            + archive + '</section>')


def render_market_outlook_archive(events, layout):
    latest_update = ''
    archive_time = None
    if events:
        latest = max(events, key=lambda event: _timestamp(event["at"]))
        archive_time = _timestamp(latest["at"])
        target = {"id": latest["forecast_id"] if latest["type"] == "review" else latest["id"]}
        latest_update = (
            '<p class="market-latest-update"><strong>予想・結果の最終更新</strong>'
            f'<a href="{market_forecast_url(target)}">'
            f'<time datetime="{escape(latest["at"], quote=True)}">{_event_day(latest["at"])}</time> JST</a></p>')
    content = ('<div class="container serials-page market-desk-archive">'
               '<nav class="serial-breadcrumb"><a href="/">トップ</a> / '
               '<a href="/serials/market-lab/">未来相場観測室</a></nav>'
               '<header class="serials-intro"><h1>過去の相場予想と結果</h1>'
               + latest_update
               + render_market_share(BASE_URL, "相場の予想と結果") + '</header>'
               + render_market_outlook_board(events, before=archive_time, link_to_archive=False) + '</div>')
    return layout("相場の予想と結果 | 未来相場観測室",
                  "当時公表した予想と、最終集計時点で確認できた結果を読む。",
                  BASE_URL, content, active="serials",
                  serial={"slug":"market-lab","kind":"analysis","title":"未来相場観測室"})


def build_market_outlooks(events, layout, write):
    write(BASE_URL.lstrip("/") + "index.html", render_market_outlook_archive(events, layout))
