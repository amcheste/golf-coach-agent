# Security Policy

## Supported Versions

Only the latest release is actively maintained.

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Use GitHub's [private vulnerability reporting](../../security/advisories/new) to report issues confidentially.

Please include:
- A clear description of the vulnerability
- Steps to reproduce
- Potential impact

You can expect an acknowledgement within **7 days** and a resolution or status update within **30 days**.

## Handling credentials

Golf Coach Agent connects to R-Cloud on your behalf and talks to a Vision LLM provider. That means real secrets live on disk while the tool runs. Treat them accordingly.

- **Never commit credentials.** `RAPSODO_EMAIL`, `RAPSODO_PASSWORD`, `ANTHROPIC_API_KEY`, and `OPENAI_API_KEY` belong in `.env`, which is gitignored. Do not paste them into issues, PRs, or sample logs.
- **`config/storage_state.json` is a session token.** It is gitignored and must stay that way. Anyone with the file can act as you on R-Cloud until the session expires. If you suspect it has leaked, delete the file, rotate your Rapsodo password, and re-run `scripts/initial_login.py`.
- **Treat the `.env` file as a secret.** Do not check it in, do not share it, and do not include it in screenshots or screen recordings.
- **Scrub before reporting.** When attaching terminal output to an issue or vulnerability report, redact email addresses, API keys, and any URLs that contain session tokens.
