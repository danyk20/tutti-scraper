# Security Policy

This project makes read-only HTTP requests to tutti.ch's GraphQL API and
writes local CSV/JSON files. It doesn't handle credentials, accept
untrusted input beyond CLI arguments you control, or run a server — so its
attack surface is small, but real (e.g. how URLs/paths are constructed from
input, or how dependencies are pinned).

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue:

- Preferred: use [GitHub's private vulnerability reporting](https://github.com/danyk20/tutti-scraper/security/advisories/new)
  for this repository.
- Alternatively, email vulnerability@danielkosc.eu with a description and,
  if possible, steps to reproduce.

Please allow a reasonable amount of time to respond and address the issue
before any public disclosure. This is a small, single-maintainer project —
response time is best-effort, not guaranteed on an SLA.

## Supported versions

Only the latest commit on `master` is supported. There are no long-term
maintenance branches.
