# `<project>`: how it was built

The story of this project, one entry per stage, oldest first. A newcomer should be able to read it in five minutes and understand why the project looks the way it does. The PRs hold the details; this file holds the turning points.

What's worth writing down:
- **Decisions and why.** What was chosen, what was turned down, and the reason.
- **Surprises.** What turned out harder, easier or different than expected.
- **Mistakes and the fix.** What went wrong, and the rule added so it doesn't happen again.
- **Changes of mind.** A feature that moved to later, a requirement that was dropped.

What isn't: routine progress ("wrote tests, they pass"), anything the board or the PR list already shows.

The examples below are for a made-up booking app for a yoga studio. At setup, copy this file with only the Setup entry; each later stage adds its own entry when it ends.

## `<date>`: Setup
- Repo and board created. Brain dump: `<issue link>`.

> **Example:** The owner wants students to book classes from their phones. The studio already uses a paper sign-up sheet, so the app must be simpler than paper or no one will switch.

## `<date>`: PRD approved (gate 1)

> **Example:** The first draft had accounts and passwords. Going through the clickable mockups, the owner saw that most students book once a week and would forget a password, so sign-in became a link sent by email. Payments moved to Future work: the studio takes cash at the door and that works fine.

## `<date>`: TDD approved (gate 2)

> **Example:** Planned a small server plus a database. Turned down storing bookings in a file on the server, because the host wipes files on every deploy. Chose a hosted database with a free tier. The test plan checks that two people can't book the last spot at the same moment.

## `<date>`: Build

> **Example:** Seven PRs. The booking screen took three tries: the first version hid the class time below the fold on small phones. The reviewer caught a time-zone bug (classes showed an hour off after daylight saving time), and "test both sides of a clock change" became a repo rule.

## `<date>`: Live

> **Example:** Launched at `<address>`. In the first week 40 students booked. The owner asked for a waitlist, which went on the board as the next card.
