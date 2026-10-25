# Contributing to HAYAT

Thank you for helping make HAYAT better. Every kind of contribution counts: a bug report with clear steps, a fix, a
test, a clearer sentence in the docs, a new tool or window.

## Ways to help

- **Report a bug** — use the *Bug report* issue form. Say what you asked, what HAYAT did, and what you expected.
  Paste the plain-language reply, and the **Activity** details if you have them.
- **Suggest a feature** — use the *Feature request* form. Describe the problem first, then the idea.
- **Pick an issue** — issues labelled `good first issue` are small and well-scoped; `help wanted` ones are bigger.
  Comment on the issue so nobody duplicates the work.
- **Improve the docs** — the user guide is generated from the capability catalog (see below); other docs live in
  `docs/` and are plain Markdown.

## Set up a development copy

```powershell
git clone https://github.com/<your-account>/HAYAT.git
cd HAYAT
.\setup.ps1
.\.venv\Scripts\activate
```

A model is only needed to *use* HAYAT; almost all tests run without one.

## Project layout

| Path | What lives there |
| --- | --- |
| `app/` | The application: agent loop, intent router, tools, memory, research, GUI windows (`app/gui*.py`) |
| `app/capabilities.py` | The plain-language catalog of every area HAYAT covers — drives help, planning and the user guide |
| `app/tools/` | Tool implementations registered with the permission system |
| `config/` | `settings.yaml`, `models.yaml`, `permissions.yaml` |
| `tests/` | The test suite (`test_live_*.py` are opt-in checks against real models and networks) |
| `scripts/` | Setup, diagnostics and generators |
| `docs/` | User and developer documentation |

Start reading at [docs/architecture.md](docs/architecture.md) and [docs/technical-tour.md](docs/technical-tour.md).

## Run the tests

```powershell
python -m pytest tests -q --ignore-glob="tests/test_live_*.py" --timeout=300 --basetemp=.test-runs/pytest
python -m ruff check app tests scripts
```

The offline suite has about 7,700 tests and takes 20–25 minutes on a laptop. While you work, run the files
related to your change, then the whole suite before you open a pull request. Use a `--basetemp` path **without
spaces**; a few tests parse paths out of text.

Live checks are opt-in and use real models or the network:

```powershell
$env:HAYAT_LIVE = "1"; python -m pytest tests/test_live_understanding.py -q
```

## The rules every change follows

1. **Code, tests and docs together.** A user-visible change includes a test that fails without it and an update to
   the relevant doc. If you change `app/capabilities.py`, regenerate the guide with
   `python scripts/generate_user_guide.py`; if you change tools, run `python scripts/generate_reference.py`.
2. **Honest behaviour.** HAYAT only says something was done when a tool result shows it. Never add code that claims
   success it has not verified, invents sources, or hides a failure.
3. **Plain answers.** Results reach people as plain language (`app/plain_output.py`, `app/observed_reply.py`),
   never as raw dictionaries or JSON.
4. **Zero cost.** No paid APIs or services, and no code that could start billing someone.
5. **Privacy and permission.** New tools register a permission class. Anything that acts on the outside world
   (sending, posting, buying, signing in) goes through the confirmation gate. Do not collect personal data the
   feature does not need.
6. **Respect other people's systems.** Honour `robots.txt` and published rate limits. No CAPTCHA solving, access
   bypass, stealth scraping, or tools for identifying unknown people from their faces.
7. **Small, focused pull requests** with a clear description of what changed, why, and how you tested it.

## Commit messages

Use a short conventional prefix — `feat:`, `fix:`, `docs:`, `test:`, `refactor:` — and say what changed and why:

```
fix: keep greetings with a time word out of web search
```

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make your change with its test and docs.
3. Run the related tests, the full offline suite, and `ruff`.
4. Open the pull request and fill in the template. The automatic checks must pass.

A maintainer will review it. Questions are welcome — open a discussion or ask in the issue.

## Licence

By contributing, you agree that your contribution is released under the project's
[GNU AGPL-3.0](LICENSE) licence. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
