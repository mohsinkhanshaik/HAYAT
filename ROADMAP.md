# HAYAT roadmap

Where HAYAT is going next, and where help makes the biggest difference. Every item links to an open issue: comment
there before you start so work is not duplicated. New ideas are welcome in
[Discussions](https://github.com/mohsinkhanshaik/HAYAT/discussions).

## 1. Honest and uncensored answers

HAYAT answers every topic plainly and completely — law, medicine, security, money, politics, religion — without
lectures or disclaimers nobody asked for. It still never acts against a specific person or system, attacks what the
owner does not own, or hands over credentials. Help wanted:

- **Measure false refusals.** An open benchmark of lawful but sensitive questions, scored for refusals, hedging
  and unrequested disclaimers, so every model and prompt change can be compared.
- **Better uncensored models.** Evaluate new abliterated and uncensored local models against HAYAT's hard
  question set and publish the results.
- **Answer quality on hard topics.** Correct legal status, real mechanisms, cited sources — not a summary of
  the easy part.

## 2. Deeper and more powerful

- **Deep reasoning mode:** check multi-step answers (self-consistency, verification of arithmetic and plans)
  before replying.
- **Full test suite in CI:** today the automatic checks run the core tests; run all ~7,700 offline tests in
  parallel shards.
- **Faster start:** Tor's cold start takes about a minute and the 35B model about 20 seconds to load; warm
  them sooner and show progress.
- **More free research sources:** more providers in the search chain, all free and robots.txt-respecting.

## 3. Everyone can run it

- **Linux and macOS:** much of the core is portable; the desktop app and Windows-only tools need equivalents.
- **Smaller machines:** a good default for 16 GB RAM and no GPU (HAYAT-Open-4B or another small model).
- **More languages:** guide translations and better non-English conversation.

## 4. Polish

- Empty states, contrast and layout in every window; screenshots in docs.
- More "Try saying" examples in the capability catalog (`app/capabilities.py`), which generates the user guide.

## Never on the roadmap

Paid APIs or anything that can bill a user; tools to identify unknown people from their faces; CAPTCHA solving,
access bypass or stealth scraping; anything that acts on the outside world without the owner's approval.
