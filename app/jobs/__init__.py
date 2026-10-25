"""Jobs: find public postings, understand them and the owner's CV, match the two honestly, prepare
applications, track them, and send an application only after the owner confirms it.

* sources.py: keyless public job APIs whose robots.txt allows reading, each credited with a link.
* text.py: HTML to text and the skill vocabulary both sides are read with.
* understand.py: what a posting asks for (required and nice-to-have skills, years, level, work mode,
  where you must live, languages, salary, warning signs).
* profile.py: what the owner's CV says (skills with evidence, years, titles, education, languages)
  plus the owner's own preferences.
* match.py: a score with every part shown, hard eligibility checks, and the gaps.
* store.py: jobs, matches and applications in data/jobs/jobs.db.
* apply.py: the application packet (cover letter, answers sheet, the CV unchanged) and sending it.
* watcher.py: the background round: search, match, prepare strong matches, follow-up reminders.
* tools.py: tools, chat routes and replies.

Applying acts as the owner toward an employer, so nothing is ever sent unattended: sending is a
proposal in the confirmation window, and web-form applications are finished by the owner on the
employer's own page.
"""
