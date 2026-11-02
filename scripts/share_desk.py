"""Free, static X promotion desk for the fictional newspaper.

Candidates are picked by publication recency/category diversity, never by
fabricated popularity or a claim of editorial merit. All actions are manual.
No X API, paid AI, networking, new storage or scheduled posting.
"""
from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import urlencode

SITE = "https://veripsa.com"
TRACKING = "?utm_source=x&utm_medium=social&utm_campaign=share_desk"


def real_date(article: dict) -> str:
    """Never use the fictional five-years-later dateline as a real publish date."""
    return article.get("published_at") or article["source"]["published_at"][:10]


def select_candidates(articles: list[dict], limit: int = 3) -> list[dict]:
    """Newest real publication day first; then other days if necessary.

    Within each period, prefer different categories before repeats. Does NOT
    purport to predict which articles are funny or most popular.
    """
    if limit <= 0:
        return []
    newest = sorted(articles, key=lambda a: (real_date(a), a["source"]["published_at"]),
                    reverse=True)[:40]
    if not newest:
        return []
    newest_day = real_date(newest[0])[:10]
    groups = [
        [a for a in newest if real_date(a)[:10] == newest_day],
        [a for a in newest if real_date(a)[:10] != newest_day],
    ]
    chosen = []
    seen = set()
    categories = set()
    for group in groups:
        for distinct_first in (True, False):
            for a in group:
                if len(chosen) >= limit:
                    break
                if a["slug"] in seen:
                    continue
                if distinct_first and a["category"] in categories:
                    continue
                seen.add(a["slug"])
                categories.add(a["category"])
                chosen.append(a)
    return chosen


def _short(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit-1].rstrip() + "…"


def draft(article: dict, *, site: str = SITE) -> dict:
    """One clearly fictional, bounded, human-editable X draft per story."""
    href = site + "/articles/" + article["slug"] + "/"
    tagged = href + TRACKING
    title = _short(article["title"], 58)
    teaser = _short(article["summary"], 62)
    year = str(article["fiction_at"])[:4]
    if len(year) != 4 or not year.isdigit():
        year = "5年後"
    heading = "【" + year + "年の架空ニュース】\n" + title
    ending = "\n\nこんな未来、ありそう？\n※創作 #未発生新聞"
    # Keep the literal copied draft short, even when the human pastes the
    # entire tracking URL into X instead of using the X-intent link.
    fixed = heading + ending + "\n\n" + tagged
    available = max(0, 265 - len(fixed) - 2)
    excerpt = _short(teaser, available) if available else ""
    text = heading + ("\n\n" + excerpt if excerpt else "") + ending
    copied = text + "\n\n" + tagged
    if len(copied) > 265:
        raise ValueError("X draft exceeds the conservative length limit")
    intent = "https://x.com/intent/tweet?" + urlencode({"text": text, "url": tagged})
    return {"text": text, "copy": copied, "intent": intent,
            "tagged_url": tagged, "article_url": href}


def render(articles: list[dict], image_for, photo_allowed,
           *, limit: int = 3) -> str:
    """Render a stand-alone public/noindex desk with manual links and previews."""
    candidates = select_candidates(articles, limit)
    newest_day = real_date(candidates[0])[:10] if candidates else None
    sections = []
    for position, article in enumerate(candidates, start=1):
        d = draft(article)
        slug = article["slug"]
        image = image_for(article)
        local = image.split("?", 1)[0]
        if not (local.startswith("/og/articles/") or local.startswith("/photos/")):
            raise ValueError("Share image must be an approved local PNG/JPEG")
        if not local.endswith((".png", ".jpg")):
            raise ValueError("Share image must be a raster PNG or JPEG")
        photo = photo_allowed(article)
        credit = ""
        if photo:
            credit = ("イメージ写真："
                      + html.escape(article["photo"]["credit"])
                      + " / Unsplash（画像を添付する際は出典にも配慮）")
        else:
            credit = "編集部制作の架空ニュース用イラスト。事件の記録写真ではありません。"
        img_name = slug + (".jpg" if local.endswith(".jpg") else ".png")
        published = real_date(article)
        date_only = published[:10]
        stamp = datetime.fromisoformat(date_only)
        date_label = f"{stamp.month}月{stamp.day}日公開"
        if date_only != newest_day:
            date_label += "・前の発行分"
        # Do not include fake vote/readership counts in a static promotion desk.
        sections.append(f'''<article class="item">
<div class="item-head"><span class="edition-number">候補 {position}</span>
<span>{html.escape(article["category"])} / {date_label}</span></div>
<h2>{html.escape(article["title"])}</h2>
<p class="intro">投稿前に記事の面白さ・内容と画像の一致・権利表記を確認してください。</p>
<div class="content">
<div class="visual">
<img src="{html.escape(image, quote=True)}" alt="{html.escape(article["title"], quote=True)}のX共有用画像" loading="lazy" decoding="async" width="1200" height="630">
<p class="credit">{credit}</p>
<a class="image-link" href="{html.escape(image, quote=True)}" download="{html.escape(img_name,quote=True)}">画像を保存・開く ↗</a>
</div>
<div class="post">
<label for="copy-{position}">X投稿文（必要なら編集して使用）</label>
<textarea id="copy-{position}" readonly rows="9" aria-label="候補{position}のX投稿文">{html.escape(d["copy"])}</textarea>
<div class="actions">
<button type="button" data-copy-post>投稿文をコピー</button>
<a href="{html.escape(d["intent"], quote=True)}" target="_blank" rel="noopener noreferrer external">Xで下書きを開く ↗</a>
</div>
<p class="post-feedback" role="status" aria-live="polite" data-copy-status></p>
</div>
</div>
<p class="article-link"><a href="{html.escape(d["article_url"], quote=True)}" target="_blank" rel="noopener noreferrer">実際の掲載記事を読む ↗</a></p>
</article>''')

    no_results = '<p>公開済みの記事がありません。掲載後に候補が自動表示されます。</p>'
    return f'''<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="theme-color" content="#f8f5ed">
<meta name="description" content="未発生新聞の公開済み記事からX紹介候補を確認する投稿支援ページです。">
<title>X投稿デスク | 未発生新聞</title>
<style>
:root{{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;color:#24332e;background:#f8f6f0}}
*{{box-sizing:border-box}}body{{margin:0}}
main{{max-width:1130px;margin:auto;padding:30px 18px 85px}}
a{{color:#9f392a;text-decoration:underline;text-underline-offset:3px}}
h1{{font-size:clamp(27px,4vw,43px);margin:15px 0 8px;letter-spacing:-.05em}}
h2{{font-size:clamp(19px,2.5vw,25px);line-height:1.55;letter-spacing:-.045em;margin:14px 0 9px}}
.lead{{font-size:14px;line-height:1.9;color:#617068;max-width:79ch}}
.guide{{background:#edeee5;border-left:3px solid #c44230;padding:12px 17px;line-height:1.8;font-size:12px;margin:23px 0}}
.item{{border-top:4px double #26332d;padding:24px 0 29px;margin-top:24px}}
.item-head{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;font-size:12px;color:#617068}}
.edition-number{{font-weight:900;color:#a43b2b}}
.intro,.credit{{font-size:12px;line-height:1.75;color:#657168}}
.content{{display:grid;grid-template-columns:1.05fr 1fr;gap:22px;margin-top:14px}}
.visual,.post{{min-width:0}}
.visual img{{width:100%;height:auto;aspect-ratio:1200/630;object-fit:contain;border:1px solid #cacabd;background:#e5e8e0;display:block}}
.image-link{{font-size:12px;font-weight:800}}
.post label{{display:block;font-size:12px;font-weight:900;margin-bottom:8px}}
textarea{{width:100%;resize:vertical;min-height:215px;background:#fffefb;color:#26332d;border:1px solid #babfb6;
padding:13px 15px;font:13px/1.75 system-ui,-apple-system,"Hiragino Kaku Gothic ProN",sans-serif}}
textarea:focus-visible,button:focus-visible,a:focus-visible{{outline:3px solid #c44230;outline-offset:3px}}
.actions{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px}}
button{{font:800 13px system-ui;cursor:pointer;border:none;color:white;background:#a74130;padding:12px 16px;border-radius:3px}}
button:hover{{background:#833223}}.actions a{{font-size:12px;font-weight:800}}
.post-feedback{{min-height:20px;font-size:12px;color:#68776c}}
.article-link{{font-size:12px;margin:13px 0 0}}
.note{{font-size:11px;line-height:1.85;color:#66746c}}
@media(max-width:690px){{.content{{grid-template-columns:1fr}}.post{{margin-top:12px}}.item{{padding-top:20px}}}}
</style></head><body><main>
<p><a href="/">未発生新聞へ戻る</a> · <a href="/image-review/">公開画像の検品一覧</a></p>
<h1>編集部のX投稿デスク</h1>
<p class="lead">掲載済みの記事から、最新発行日を優先し、ジャンルを分散させた紹介候補を表示しています。
面白さのランキングでも自動投稿予定表でもありません。最終的に紹介する記事は人が選んでください。</p>
<div class="guide"><strong>使い方：</strong>画像と本文を確認 → 投稿文をコピーまたはXの下書きを開く →
必要なら画像を手動で添付して投稿。画像が自動添付されることはありません。
記事が更新されるとこのページの候補も自動更新されます。Xのアカウント連携・有料APIは不要です。</div>
<div class="items">{''.join(sections) if sections else no_results}</div>
<p class="note">候補は{len(candidates)}件。実際の発行日を表示し、5年後の日付を実際の掲載日と混同しません。
生成した投稿文は編集のたたき台です。コメント・投票・閲覧数などの実績を捏造しません。
このページは公開されていますが検索登録対象外で、運営者専用の秘密情報を保存しません。</p>
</main>
<script>
document.querySelectorAll("[data-copy-post]").forEach(function(button){{
  button.addEventListener("click",async function(){{
    var item=button.closest(".item");
    var box=item.querySelector("textarea");
    var feedback=item.querySelector("[data-copy-status]");
    try{{
      if(navigator.clipboard && navigator.clipboard.writeText){{
        await navigator.clipboard.writeText(box.value);
      }}else{{
        box.focus();box.select();
        if(!document.execCommand("copy"))throw new Error("copy unavailable");
      }}
      feedback.textContent="投稿文をコピーしました。";
    }}catch(error){{
      box.focus();box.select();
      feedback.textContent="投稿文を選択しました。手動でコピーしてください。";
    }}
  }});
}});
</script></body></html>'''
