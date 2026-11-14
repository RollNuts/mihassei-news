"""Shared, first-party reader comment form for stories and photo issues."""
from html import escape


def render_reader_comments(key, *, heading="読者のツッコミ", placeholder="未来のニュースにひとこと…"):
    key_attribute = "data-comments-key" if key.startswith("photo-issue:") else "data-article"
    return f'''<section class="reader-comments" id="reader-comments" data-comments {key_attribute}="{escape(key, quote=True)}" aria-labelledby="comments-heading">
<div class="reader-comments-heading">
<h2 id="comments-heading">{escape(heading)} <small data-comments-count></small></h2>
<p>投稿内容は公開されます。</p>
</div>
<div class="reader-comments-list" data-comments-list></div>
<p class="reader-comments-status" data-comments-status role="status">コメントを読み込み中…</p>
<noscript><p>コメントの閲覧・投稿にはJavaScriptを有効にしてください。</p></noscript>
<form class="reader-comments-form" data-comments-form>
<label for="reader-nickname">ニックネーム <span>（任意・16文字以内）</span></label>
<input id="reader-nickname" name="nickname" type="text" maxlength="16" autocomplete="nickname" placeholder="匿名の未来人">
<label for="reader-body">コメント</label>
<textarea id="reader-body" name="body" minlength="2" maxlength="180" rows="3" required placeholder="{escape(placeholder, quote=True)}"></textarea>
<div class="comment-form-footer"><span>2〜180文字。URL・個人情報・誹謗中傷は投稿できません。</span>
<button type="submit">コメントを投稿</button></div>
</form>
</section>'''
