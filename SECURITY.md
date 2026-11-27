# Security policy

This is a university project, not a production courier system. Security
reports are still welcome.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
reporting instead: open the **Security** tab of this repository and choose
**Report a vulnerability**. Include the steps to reproduce it and what an
attacker could do.

## What to expect

Reports are read on a best-effort basis by a single maintainer. There is no
guaranteed response time.

## Scope notes

- API keys are stored only as SHA-256 hashes and shown once when created.
- Webhook URLs must be public HTTPS addresses; private and local addresses are
  rejected.
- Never commit real credentials. `.env.local` is ignored by Git; use
  `.env.example` as the template.
