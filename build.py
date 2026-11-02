#!/usr/bin/env python3
"""Build the static Japanese fictional newspaper. Standard library only."""
import datetime as dt
import html
import json
import pathlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

ROOT=pathlib.Path(__file__).parent
OUT=ROOT/"dist"
SITE="https://veripsa.com"
ENTRIES=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))
def esc(s):return html.escape(str(s),quote=True)
def fmt(s):return dt.datetime.fromisoformat(s[:10]).strftime("%Y年%m月%d日")
def url(a):return "/articles/"+quote(a["slug"])+"/"
def ts(a):
    date=a.get("fiction_date","2031-11-02")
    time=a.get("fiction_time","15:46")
    return fmt(date)+" "+time
def card(a):
    search=" ".join([a["title"],a["summary"],a["category"]]+a["paragraphs"])
    decade=a["fiction_date"][:3]+"0年代"
    return f'''<article class="story-card" data-archive-card data-category="{esc(a["category"])}" data-decade="{esc(decade)}" data-search="{esc(search)}"><div class="story-meta"><span class="category">{esc(a["category"])}</span><span>{esc(ts(a))}</span><span class="red">架空</span></div><h3><a href="{url(a)}">{esc(a["title"])}</a></h3><p>{esc(a["summary"])}</p><div class="story-foot">この記事は架空のニュースです　→</div></article>'''
def layout(content,title,description,canonical,active=""):
    nav="".join(f'<a href="{href}" {('aria-current=page' if active==label else '')}>{label}</a>' for href,label in [("/","トップ"),("/archive/","記事一覧"),("/about/","この新聞について"),("/feed.xml","RSS")])
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} | 未発生新聞</title><meta name="description" content="{esc(description)}"><link rel="canonical" href="{SITE}{canonical}"><meta name="robots" content="index,follow"><meta property="og:type" content="article"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}"><meta property="og:url" content="{SITE}{canonical}"><meta property="og:site_name" content="未発生新聞"><link rel="icon" type="image/svg+xml" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/style.css"><link rel="alternate" type="application/rss+xml" title="未発生新聞 RSS" href="/feed.xml"></head><body><a href="#main" class="skip">本文へ</a><div class="strip"><div class="container"><span>5年後の続きが、ここにある。</span><span><strong>FICTION</strong>　すべて架空のニュースです</span></div></div><header class="masthead container"><div class="mast-kicker">まだ起きていないことを、まじめに報道する。</div><a class="brand" href="/" aria-label="未発生新聞 トップ">未発生<span class="brand-last">新聞</span></a><span class="brand-en">MIHASSEI SHINBUN</span><p class="mast-sub">今日の話題から、ちょうど5年後の架空の続報を。</p></header><nav class="nav-bar"><div class="container nav-inner">{nav}</div></nav><main id="main">{content}</main><footer class="footer"><div class="container"><div class="footer-grid"><div><a href="/" class="footer-brand">未発生新聞</a><p class="footer-copy">今日の話題から着想した「5年後のもしも」を伝えるフィクションメディア。掲載の人物・企業・事件・発言はすべて架空です。</p></div><div class="footer-nav"><a href="/">トップ</a><a href="/archive/">記事一覧</a><a href="/about/">運営方針</a><a href="/feed.xml">RSS</a></div></div><div class="footer-bottom"><span>© 未発生新聞 / Fictional News Experiment</span><span>※これは実在のニュースサイトではありません。</span></div></div></footer><script src="/assets/site.js" defer></script></body></html>'''
def write(urlpath,content):
    path=OUT/urlpath.strip("/")/"index.html"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(content,encoding="utf-8")
def main():
    OUT.mkdir(exist_ok=True)
    import shutil
    shutil.copytree(ROOT/"assets",OUT/"assets",dirs_exist_ok=True)
    articles=sorted(ENTRIES,key=lambda a:(a.get("published_at",""),a.get("fiction_date","")),reverse=True)
    lead=articles[0]
    others=articles[1:4]
    side="".join(f'<div class="brief-card"><div class="story-meta"><span class="category">{esc(a["category"])}</span> <span>{esc(ts(a))}</span></div><h3><a href="{url(a)}">{esc(a["title"])}</a></h3><p>{esc(a["summary"])}</p></div>' for a in others)
    home=f'''<div class="container index-main"><section class="hero-grid"><div class="lead"><div class="lead-media" role="img" aria-label="架空の未来ニュースを表す抽象的なビジュアル"><div class="overlay"><span class="issue-code">THE UNHAPPENED TIMES · FICTION</span><span class="large-year">{esc(lead["fiction_date"][:4])}</span></div></div><div class="story-meta"><span class="category">{esc(lead["category"])}</span><span>{esc(ts(lead))}</span><span class="red">架空速報</span></div><h1><a href="{url(lead)}">{esc(lead["title"])}</a></h1><p class="dek">{esc(lead["summary"])}</p><a class="read-more" href="{url(lead)}">記事を読む</a></div><aside class="side-brief"><div class="side-title"><h2>TOP STORIES</h2><span>5 YEARS LATER</span></div>{side}</aside></section><section class="section-block"><div class="section-head"><h2>まだ起きていないニュース</h2><a href="/archive/">記事をすべて見る ↗</a></div><div class="story-grid">{''.join(card(a) for a in articles)}</div></section><div class="banner"><div><span class="small-label red">ABOUT THIS PAPER</span><h2>事実は今日。記事は5年後。</h2><p>話題の出来事をそのまま転載せず、そこから考えた「もしも」を、実在しない企業や人物が登場する新聞記事として描きます。すべて創作です。</p></div><a href="/about/">新聞の読み方</a></div></div>'''
    write("/",layout(home,"今日から5年後の架空ニュース","話題の出来事をヒントに、5年後の架空ニュースを真顔で伝えるフィクションメディア。","/","トップ"))
    categories=["すべて"]+sorted(set(a["category"] for a in articles))
    tabs="".join(f'<button class="filter-button" type="button" data-category-filter="{esc(c)}" aria-pressed="false">{esc(c)}</button>' for c in categories)
    decades=["すべて"]+sorted(set(a["fiction_date"][:3]+"0年代" for a in articles))
    options="".join(f'<option value="{esc(d)}">{esc(d)}</option>' for d in decades)
    archive=f'''<div class="container archive-page"><header class="page-intro"><span class="eyebrow">FICTION ARCHIVE</span><h1>まだ起きていない記事一覧</h1><p>未来の日付で報じる架空ニュース。元ネタの転載は禁止・無断転載は行いません。</p></header><div class="filters" aria-label="カテゴリーで絞り込み">{tabs}</div><div class="search-row"><input type="search" class="search-input" data-search-input aria-label="記事を検索" placeholder="記事のタイトル・内容を検索"><select class="decade-select" data-decade-filter aria-label="年代で絞り込み">{options}</select><span class="result-counter" data-result-count>{len(articles)}件の記事</span></div><div class="archive-list">{''.join(card(a) for a in articles)}</div><p class="no-results" data-no-results hidden>一致する記事はありません。</p></div>'''
    write("/archive/",layout(archive,"記事一覧","架空のニュース記事一覧。","/archive/","記事一覧"))
    for a in articles:
        original=a.get("source_url")
        source=f'<p>着想元：<a href="{esc(original)}" target="_blank" rel="noopener noreferrer">元の公開情報を見る（外部サイト）</a></p>' if original and original.startswith("https://") else "<p>着想元：創作上のテーマ（特定のニュース・投稿の転載ではありません）</p>"
        body="".join("<p>"+esc(p)+"</p>" for p in a["paragraphs"])
        banner=f'''<div class="fiction-warning">【フィクション】この記事は実際の報道ではありません。企業名・人物・発言・出来事は架空です。公開日と、5年後に設定した架空のニュース日時は異なります。</div>'''
        article=f'''<div class="container article-page"><article class="article-shell"><header class="article-header"><span class="eyebrow">架空速報 · FICTION</span><div class="story-meta" style="margin-top:17px"><span class="category">{esc(a["category"])}</span><time>{esc(ts(a))}</time><span>架空の報道日時</span></div><h1>{esc(a["title"])}</h1><p class="dek">{esc(a["summary"])}</p><div class="story-meta"><span>実際の掲載日：{esc(fmt(a.get("published_at","2026-11-02")))}</span></div></header>{banner}<div class="article-body">{body}</div><div class="article-end"><div class="story-meta">{source}</div><a href="/archive/">記事一覧へ戻る ↗</a></div><section class="article-related"><div class="section-head"><h2>ほかの架空ニュース</h2></div><div class="story-grid">{''.join(card(x) for x in articles if x["slug"]!=a["slug"])[:24000]}</div></section></article></div>'''
        write(url(a),layout(article,a["title"],a["summary"],url(a)))
    about=f'''<div class="container archive-page"><header class="page-intro"><span class="eyebrow">EDITORIAL POLICY</span><h1>この新聞について</h1><p>事実の続きに、ありそうでない5年後を。</p></header><div class="about-copy"><h2>未発生新聞とは</h2><p>インターネットやニュースで話題になった「いま起きていること」をヒントに、5年後の同じ月日・時刻に何が起きているかを想像する架空のニュースメディアです。記事内の出来事は実際に発生したものではありません。</p><h2>記事を作るルール</h2><p>発信時点で話題の現象から着想を得ますが、実在する記事やSNS投稿の文章・画像・スクリーンショットを転載しません。架空の社名や人物を使用し、実在する個人や企業に根拠のない事件・不祥事を結びつけません。</p><p>「速報」「発行日時」は架空世界内の演出です。元の話題を確認できる場合は別途出典URLを付け、引用や要約が必要な際は権利と誤認防止に配慮します。</p><h2>5年後の同日時</h2><p>記事の架空日時は、着想元の話題が公開された日時から5年後に合わせることを原則とします。日付と時刻が検証できないテーマでは、架空の報道日時として明示します。</p><h2>画像について</h2><p>素材画像を用いる場合はオリジナル生成または使用許諾を確認した素材に限定します。AI生成画像には架空イメージであることを明示します。</p><h2>お問い合わせ・訂正</h2><p>掲載内容についてのご指摘は、公開リポジトリのIssuesからお願いします。フィクションであっても誤認を招く表現は訂正・削除します。</p><p><a href="https://github.com/RollNuts/mihassei-news/issues" rel="noopener noreferrer">GitHub Issues ↗</a></p></div></div>'''
    write("/about/",layout(about,"この新聞について","架空ニュースの制作・権利・公開方針。","/about/","この新聞について"))
    write("/404/",layout('<div class="container not-found"><h1>404</h1><p>この未来はまだ発生していません。</p><a class="read-more" href="/">トップへ戻る</a></div>',"記事が見つかりません","記事が見つかりません。","/404/"))
    (OUT/"404.html").write_text((OUT/"404"/"index.html").read_text(encoding="utf-8"),encoding="utf-8")
    rss=ET.Element("rss",version="2.0");channel=ET.SubElement(rss,"channel")
    for tag,value in [("title","未発生新聞"),("link",SITE),("description","5年後の架空ニュース。すべてフィクションです。"),("language","ja")]:ET.SubElement(channel,tag).text=value
    for a in articles:
        item=ET.SubElement(channel,"item")
        for tag,value in [("title","【フィクション】"+a["title"]),("link",SITE+url(a)),("guid",SITE+url(a)),("description",a["summary"]+"　※架空ニュースです。")]:ET.SubElement(item,tag).text=value
    ET.ElementTree(rss).write(OUT/"feed.xml",encoding="utf-8",xml_declaration=True)
    sitemap=ET.Element("urlset",xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for loc in ["/","/archive/","/about/"]+[url(a) for a in articles]:ET.SubElement(ET.SubElement(sitemap,"url"),"loc").text=SITE+loc
    ET.ElementTree(sitemap).write(OUT/"sitemap.xml",encoding="utf-8",xml_declaration=True)
    (OUT/"robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: "+SITE+"/sitemap.xml\n",encoding="utf-8")
    print(f"Built {len(articles)} fictional articles -> {OUT}")
if __name__=="__main__":main()
