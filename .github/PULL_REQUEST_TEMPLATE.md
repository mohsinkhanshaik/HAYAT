## What this changes

<!-- One or two sentences: what is different for someone using HAYAT? -->

## Why

<!-- The problem it solves. Link the issue: Fixes #123 -->

## How it was tested

<!-- The tests you added or changed, and the commands you ran. -->

- [ ] Added or updated a test that fails without this change
- [ ] Ran the related tests and the full offline suite (`python -m pytest tests -q --ignore-glob="tests/test_live_*.py"`)
- [ ] `python -m ruff check app tests scripts` passes
- [ ] Updated the docs (and regenerated the guide/reference if capabilities or tools changed)

## Checklist

- [ ] No paid service or API
- [ ] Outward actions (send, post, buy, sign in) wait for the owner's confirmation
- [ ] Results are shown as plain language, never raw data
- [ ] No secrets, tokens or personal data in the code, tests or screenshots
