# Security and privacy

JobZoom is designed to keep personal configuration and runtime evidence out of Git.

Never commit:

- config/jobzoom.json
- config/cv-master.md
- gateway tokens
- Telegram targets tied to a real account
- SQLite runtime databases
- generated reports or CVs
- application history
- OpenClaw secret files
- private keys

Use environment variables or the local OpenClaw secrets store for gateway credentials.

If you discover a security issue, do not publish credentials or personal job data in a public issue. Contact the repository owner privately.
