"""Reader-triggered sharing controls for factual market pages."""
from html import escape
from urllib.parse import urlencode, urlsplit


def render_market_share(path, title):
    target = urlsplit(path)
    if (target.scheme not in ("", "https") or target.netloc not in ("", "veripsa.com")
            or not target.path.startswith("/serials/market-lab/")
            or not target.path.endswith("/") or ".." in target.path):
        raise ValueError("Market sharing requires its own canonical page")
    canonical = "https://veripsa.com" + target.path
    intent = "https://x.com/intent/tweet?" + urlencode({
        "text": title + "｜実データの市場分析", "url": canonical,
    })
    return (
        '<div class="market-share" role="group" aria-label="このページを共有">'
        f'<a href="{escape(intent, quote=True)}" target="_blank" '
        'rel="noopener noreferrer external">Xで共有</a>'
        f'<button type="button" data-copy-url="{escape(canonical, quote=True)}">'
        '<span>リンクをコピー</span></button>'
        f'<button type="button" data-native-share data-share-url="{escape(canonical, quote=True)}" '
        f'data-share-title="{escape(title, quote=True)}" hidden>スマホで共有</button>'
        '</div>'
    )
