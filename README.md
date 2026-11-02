# 未発生新聞 — Mihassei Shinbun

**Fictional news only.** 今日話題の出来事をきっかけに、5年後の同じ日時に起きるかもしれない「架空の続報」を報じる創作メディアです。

- Production domain: https://veripsa.com (Cloudflare configuration required)
- Hosting: Cloudflare Pages (static assets)
- Build: \`python3 build.py\` (Python 3.11+, no third-party packages)
- Output directory: \`dist\`
- Articles: \`content/articles.json\`
- Source repository: https://github.com/RollNuts/mihassei-news

## Cloudflare Pages deployment

1. Cloudflare → Workers & Pages → Create application → Pages → Connect to Git → select this repository.
2. Production branch: \`main\`. Framework preset: None.
3. Build command: \`python3 build.py\`; build output: \`dist\`.
4. Deploy; verify the generated \`*.pages.dev\` URL.
5. In Pages → Custom domains, add \`veripsa.com\`. Ensure the domain is managed by Cloudflare and review preexisting DNS/routes before switching. \`www\` is optional.

**No DNS updates, domain routing changes or Cloudflare deploy have been performed by this repository.**

## Publishing policy

- All pieces are fiction; never represent fabricated events as real.
- Identify the story and page as fiction, including social previews and headlines.
- Do not reproduce source text, screenshots, social profiles or third-party news images.
- Prefer clearly fictional company and character names. Verify names are not confusingly similar to real organizations before publishing.
- A trending-source URL may be stored in \`source_url\` for provenance; keep the story entirely original.
- The fictional timestamp should equal the source timestamp plus five calendar years, including hour/minute. If a timestamp is unknown, label it as fictional rather than inventing a source time.
- Verify every new piece before publication. Future automated publishing should gate on source freshness, duplicate detection, safety and non-infringement review.
- Never commit API keys. Use GitHub Actions secrets if integrating an AI generation provider later.

## Automation roadmap

A separate scheduled GitHub Action can fetch publicly available trend metadata, propose original fictional story candidates, generate original imagery with an approved provider, run checks, and commit a reviewed article. **No unattended AI generation is enabled yet**; it requires an API credential and a safe publication policy. The site itself is ready for continuous Git-based deployment.
