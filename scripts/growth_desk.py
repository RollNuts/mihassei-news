"""Read-only, noindex growth review for already-published fictional news.

This page never creates a tracking event. Its one dynamic GET uses existing
free Cloudflare engagement counters. No paid analytics, accounts or cookies.
"""
from __future__ import annotations

import html
import json

MAX_STORIES = 20
SITE = "https://veripsa.com"


def real_date(article: dict) -> str:
    return article.get("published_at") or article["source"]["published_at"][:10]


def listed_articles(articles: list[dict]) -> list[dict]:
    """Keep dashboard payload bounded and use real, not fictional, dates."""
    sorted_articles = sorted(
        articles, key=lambda a:(real_date(a), a["source"]["published_at"]),reverse=True
    )[:MAX_STORIES]
    return [
        {
            "slug":a["slug"], "title":a["title"],
            "category":a["category"], "published":real_date(a)[:10]
        }
        for a in sorted_articles
    ]


def render(articles: list[dict]) -> str:
    rows = listed_articles(articles)
    serialized = html.escape(json.dumps(rows,ensure_ascii=False,separators=(",",":")),
                             quote=True)
    return f'''<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#f8f6f0">
<meta name="description" content="未発生新聞の集客を匿名の参考値で検証する編集用ビューです。">
<title>集客の実測 | 未発生新聞</title>
<style>
:root{{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;
color:#293731;background:#f7f6ef}}
*{{box-sizing:border-box}}
body{{margin:0}}main{{max-width:1180px;margin:auto;padding:28px 18px 85px}}
a{{color:#964334;text-decoration:underline;text-underline-offset:3px}}
a:focus-visible,select:focus-visible{{outline:3px solid #af4a34;outline-offset:3px}}
h1{{font-size:clamp(27px,4vw,43px);letter-spacing:-.04em;margin:15px 0 6px}}
h2{{font-size:clamp(20px,3vw,27px);letter-spacing:-.03em;margin:33px 0 12px}}
p{{line-height:1.85}}
.lead{{color:#627068;max-width:78ch;font-size:13px}}
.kicker{{display:block;color:#ad4935;font-weight:900;font-size:11px;letter-spacing:.15em}}
.status{{font-size:12px;color:#5d6e65;padding:12px 0}}
.notice{{border-left:4px solid #ba5740;background:#ebeee5;padding:13px 18px;
font-size:12px;line-height:1.9;margin-top:24px;color:#4e5c54}}
.stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:20px}}
.stat{{background:#fffefa;border:1px solid #d3d4c8;padding:17px}}
.stat strong{{font-size:clamp(26px,4vw,35px);font-variant-numeric:tabular-nums;display:block}}
.stat span{{font-size:11px;font-weight:800;color:#59695f}}
.stat small{{font-size:10px;color:#6a746d;display:block;margin-top:9px;line-height:1.5}}
.panel{{background:#fffefa;border:1px solid #dbdbd0;padding:15px 16px;margin-top:11px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{padding:12px 9px;border-bottom:1px solid #e0e1d7;text-align:right;
font-variant-numeric:tabular-nums;vertical-align:top}}
th:first-child,td:first-child{{text-align:left}}
th{{color:#617168;font-size:11px;white-space:nowrap}}
td strong{{font-weight:900}}td small{{color:#778078;display:block;font-size:10px;margin-top:5px}}
.table-wrap{{overflow-x:auto}}
.categories th:not(:first-child),.categories td:not(:first-child){{min-width:92px}}
.article-table th:first-child,.article-table td:first-child{{min-width:240px}}
.article-table th:not(:first-child),.article-table td:not(:first-child){{min-width:72px}}
.controls{{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}}
.controls label{{font-size:12px;font-weight:800}}
select{{background:#fffefa;border:1px solid #bcc4ba;border-radius:3px;
padding:9px 12px;color:#283b32;font-size:12px}}
.footnote{{font-size:11px;color:#66756b;line-height:1.9}}
.action-link{{display:inline-block;margin-top:8px;background:#9c4032;color:#fffef9;
padding:11px 15px;text-decoration:none;font-size:12px;font-weight:850;border-radius:3px}}
.action-link:hover{{background:#85372d}}
[hidden]{{display:none!important}}
@media(max-width:600px){{.stats{{grid-template-columns:1fr 1fr}}.stat:last-child{{grid-column:span 2}}
main{{padding:22px 12px 65px}}.panel{{padding:9px 10px}}th,td{{padding:10px 7px}}}}
</style>
<script defer src="/assets/growth-desk.js"></script>
</head><body><main data-growth-desk data-growth-stories="{serialized}">
<p><a href="/">未発生新聞に戻る</a> · <a href="/share-desk/">X投稿デスク</a></p>
<span class="kicker">AUDIENCE INSIGHTS / 読者の入口</span>
<h1>集客の実測</h1>
<p class="lead">「記事が読まれたか」から一歩進んで、直近{len(rows)}本のうち、
どの記事にXや検索からブラウザが訪れ、何回表示されたかを確かめるページです。
公開済みの匿名集計だけを読み込み、ここを開いても閲覧数や反応数を増やしません。</p>
<div class="notice">
<strong>数字の読み方：</strong>X・検索・記事閲覧・次の記事へは実人数ではなく、
匿名ブラウザ識別子の累積参考値です。一方、PVはページの表示回数で、同じブラウザの再表示も含みます。
この記事別の計測開始以前の過去データは復元できません。
参照元やUTMの申告は厳密な証明ではなく、順位・売上・広告表示回数の根拠には使えません。
</div>
<p class="status" data-growth-status role="status" aria-live="polite">公開集計を取得しています…</p>
<section class="stats" aria-label="サイト全体の参考集計" data-growth-summary hidden>
<div class="stat"><span>サイト全体・累積ブラウザID</span><strong data-growth-visitors>—</strong>
<small>計測開始以降。複数経路を含む</small></div>
<div class="stat"><span>X由来・サイト全体</span><strong data-growth-channel-x>—</strong>
<small>記事以外への到着も含む</small></div>
<div class="stat"><span>検索由来・サイト全体</span><strong data-growth-channel-search>—</strong>
<small>検索結果の表示回数ではない</small></div>
<div class="stat"><span>サイト累計PV</span><strong data-growth-page-views>—</strong>
<small>ページ表示回数。ブラウザ数とは別</small></div>
<div class="stat"><span>直近7暦日PV</span><strong data-growth-week-page-views>—</strong>
<small data-growth-week-page-views-note>計測範囲を確認中</small></div>
</section>
<section data-growth-spike hidden>
<h2>今日の伸び方</h2>
<p class="footnote">当日PVの途中経過と、最もPVが多い時間帯を表示します。
ピーク時刻とX投稿時刻が近いだけでは原因と断定せず、同じ時間帯の流入元と上位記事を一緒に見ます。</p>
<div class="stats" aria-label="当日のPVとピーク時間">
<div class="stat"><span>今日のPV・ここまで</span><strong data-growth-today-pv>—</strong>
<small>当日途中。終了済み1日とは比較条件が違う</small></div>
<div class="stat"><span>前日のPV</span><strong data-growth-yesterday-pv>—</strong>
<small>終了済み日の比較用</small></div>
<div class="stat"><span>今日のピーク時間</span><strong data-growth-peak-hour>—</strong>
<small>JSTの1時間単位</small></div>
<div class="stat"><span>ピーク時間PV</span><strong data-growth-peak-pv>—</strong>
<small>その時間帯のページ表示回数</small></div>
</div>
<div class="panel">
<p class="footnote" data-growth-peak-sources>ピーク時間の流入元を取得しています。</p>
<p class="footnote" data-growth-peak-kinds>ピーク時間のページ種別を取得しています。</p>
<p class="footnote" data-growth-peak-top>ピーク時間の上位記事を取得しています。</p>
</div>
</section>
<section data-growth-details hidden>
<h2>ジャンル別の実測</h2>
<p class="footnote">対象は直近{len(rows)}本。記事単位の数値を合計しているため、
同じブラウザが複数の記事を読んだ場合は重複します。</p>
<div class="panel table-wrap"><table class="categories">
<thead><tr><th scope="col">ジャンル</th><th scope="col">Xから</th>
<th scope="col">検索から</th><th scope="col">記事閲覧</th><th scope="col">次の記事へ</th></tr></thead>
<tbody data-growth-categories></tbody></table></div>
<div class="controls"><h2>記事ごとの反応</h2>
<label>並べ替え <select data-growth-sort>
<option value="x">X経由</option><option value="search">検索経由</option>
<option value="weekPv">直近7日PV</option><option value="pageViews">累計PV</option>
<option value="readers">記事閲覧ブラウザ</option><option value="continuedReaders">次の記事へ</option>
<option value="published">実際の掲載日</option></select></label></div>
<p class="footnote">X・検索・閲覧・次記事へ・いいね・共有操作はブラウザ単位の累積参考値。
PV列だけは表示回数です。未計測は0に置き換えず「—」と表示します。</p>
<p class="footnote" data-growth-pv-coverage>PVの計測範囲を取得しています。</p>
<div class="panel table-wrap"><table class="article-table">
<thead><tr><th scope="col">記事</th><th scope="col">X</th><th scope="col">検索</th>
<th scope="col">7日PV</th><th scope="col">累計PV</th>
<th scope="col">閲覧</th><th scope="col">次記事へ</th>
<th scope="col">いいね</th><th scope="col">共有操作</th></tr></thead>
<tbody data-growth-articles></tbody></table></div>
</section>
<section>
<h2>改善の次の一手</h2>
<p class="footnote" data-growth-advice>実数の確認後に判断します。</p>
<a class="action-link" href="/share-desk/">X紹介候補と投稿文を見る</a>
<p class="footnote">Xへの投稿は手動。外部の有料解析・広告・SNS APIは使用しません。
本ページは本番サイトには公開しない編集用ビューで、管理者専用の秘密情報は含みません。</p>
</section>
</main></body></html>'''
