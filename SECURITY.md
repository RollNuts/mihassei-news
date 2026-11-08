# Security policy

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/RollNuts/mihassei-news/security/advisories/new) for vulnerabilities, accidentally exposed credentials, or personal data. Include the affected file or URL, reproduction steps, impact, and a proposed fix if available. Do not put secrets, personal data, or exploit details in a public issue or pull request.

Security fixes target the current `main` branch. There is no guaranteed response time or support commitment for older revisions.

## Keep contributions safe to publish

- Store credentials in environment variables or GitHub Actions secrets. Never commit `.env` files, provider tokens, private keys, or admin credentials.
- Use fictional or anonymized fixtures. Remove personal data, browser storage, account identifiers, and tokens from screenshots, logs, and error reports.
- Run local builds and offline tests without production credentials. Publishing, social posting, and infrastructure changes require a separate operational review; start with [the operations guide](docs/OPERATIONS.md).
- If a real credential reaches Git history, revoke or rotate it immediately. Removing the current file does not remove historical copies.

For ordinary reproducible bugs that do not expose sensitive information, use [Issues](https://github.com/RollNuts/mihassei-news/issues).
