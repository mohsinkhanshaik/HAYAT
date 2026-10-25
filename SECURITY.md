# Security policy

HAYAT runs tools on a real computer — files, browser, apps, camera — so security reports matter a great deal.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository
([direct link](https://github.com/mohsinkhanshaik/HAYAT/security/advisories/new)).

Include what an attacker could do, the steps to reproduce it, and the version (commit) you tested. You will get an
acknowledgement, and fixes are credited to the reporter unless you prefer otherwise.

## What counts

Especially welcome:

- a way for web pages, documents, emails or tool output to make HAYAT act without the owner's approval (prompt
  injection that reaches a tool);
- a bypass of the confirmation gate, permission classes, privacy mode or the emergency stop;
- a way to leak memory, contacts, secrets, location or files off the machine;
- traffic that skips the Tor route while Tor is turned on;
- remote-access flaws in the authenticated web client.

## Supported versions

Security fixes go to the latest commit on `main`.
