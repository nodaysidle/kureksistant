# Security Policy

## Supported versions

Security fixes are accepted for the current `main` branch and the latest published release tag.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems that could expose users to risk.

Report privately by emailing the repository owner via the address listed on their [GitHub profile](https://github.com/nodaysidle), with subject line:

`[SECURITY] kureksistant`

Include:

- A short description of the issue
- Impact (e.g. secret exposure, remote/local code execution, auth bypass)
- Steps to reproduce or a proof of concept if available
- Whether the issue is already public elsewhere

We aim to acknowledge reports within a few days.

## API key and secret leaks

If you discover that API keys, tokens, `.env` files, or `config/api_keys.json` were published in this repository, a fork, a release asset, or CI logs:

1. **Rotate/revoke the leaked credentials immediately** with the provider (DeepSeek, xAI, Gemini, Deepgram, OpenRouter, TypeSafe, etc.).
2. Notify us using the private channel above so we can remove or replace the artifact.
3. Do not paste the full secret into a public issue, PR, or gist.

Release packaging must never include `config/api_keys.json` or `.env`. The script `scripts/package_release.sh` refuses to build if those paths (or live key patterns) are present.

## Local threat model notes

Kurek’s headless daemon listens on `127.0.0.1:8790` and can drive powerful workstation tools. Treat any local process as able to call `/toggle` and `/prompt`. Do not expose that port to the network.
