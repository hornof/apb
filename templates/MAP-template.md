# `<project>`: project map

Every piece of this project, where it lives, and when it's used. Claude adds each new piece as soon as it exists. Claude checks every row after every merge, whenever a PR opens, at the end of each stage, and whenever the human asks where things stand. Claude fixes any row that is wrong. At the same moments, Claude checks the board: every issue is in the right column, and there is an issue for the current stage.

The map lives in two places with the same content: `docs/MAP.md` in the GitHub repo, and the map artifact in Claude: `<map artifact link>`. Claude edits only `docs/MAP.md` and builds the map artifact from it with apb's `scripts/map-artifact.py`. Links to repo files point at the branch the map artifact was built from.

The map lists only things that rarely change. It never copies a status, such as the current stage or whether a PR is open, because a copied status goes out of date. To see where the project stands now, open the board: `<board link>`. The one number in the map artifact, the build count, is worked out from GitHub each time the map artifact is built.

**Stages, in order:** PRD input → PRD (gate 1) → TDD (gate 2) → split the TDD into cards → build → review and merge (gate 3) → check the live product

<!-- Where a row needs a status, link GitHub's live list, for example the list of open PRs. -->
<!-- Keep the "Stages, in order" line: the map artifact adds the build count after "build", and the progress page reads the stages from it. -->

## GitHub
| Piece | What it is | When it's used |
|---|---|---|
| Repo: `<repo link>` | The code, docs and content | Every stage |
| Board: `<board link>` | Progress: one issue per early stage (PRD input, PRD, TDD), then one card per piece of work | Every stage |
| PRD input: `<issue link>` | The original description of the project | Start |
| `docs/MAP.md` | The map, in the repo. The map artifact is built from it. | Any time |

## Claude artifacts
Web pages Claude makes and hosts on claude.ai. Each one is private to its creator until shared. Kinds of artifacts and how a project can use them:

- **Design:** clickable mockups of screens. Use them to work through how the product looks and behaves before any code exists.
- **Whiteboard:** boxes, arrows and sketches that people move around together. Use it to draw the system's parts and how data flows between them, or to brainstorm features.
- **Sheets:** a spreadsheet with formulas. Use it for lists that change: requirements and the tests that check them, cost estimates, content inventories.
- **Docs:** a document people edit together. Use it for notes or drafts that aren't ready for the repo.
- **Slides:** a deck. Use it to show the project to someone outside it.
- **Data:** charts and numbers. Use it to track test results, usage or costs over time.
- **Plain page:** any web page Claude writes. Use it for review pages (for example, a grid of generated images to pick from) or for a quick prototype.

<!-- This list is also useful when writing the PRD and TDD: it shows the reviewer's options for seeing work before it's built. -->

| Piece | What it is | When it's used |
|---|---|---|
| Map artifact: `<map artifact link>` (plain page) | The map, in Claude. Built from `docs/MAP.md`; never edited by hand. | Any time |
| Progress page: `<progress page link>` (plain page) | What's done, running and left: built by apb's `scripts/progress-page.py` from the events file and GitHub; never edited by hand | While work runs |
| `<artifact name and link>` | `<for example: clickable mockups of the main screens>` | `<for example: PRD review>` |

## On the developer's computer
Claude can read and write a folder only after the developer gives it access. Kinds of things on the developer's computer a project can use:

- The local copy of the repo.
- A working folder outside the repo, for large or private files that shouldn't be committed (for example, raw generated content or reference material).
- Notes folders the developer already keeps, such as a notes app's vault.
- The Downloads folder, for files the developer brings in.
- Claude's memory for this project, which carries rules and lessons from one session to the next.
- Scheduled tasks that run Claude unattended on this computer.

<!-- The list of folders to give Claude access to is also useful in the setup step: asking for all of them at the start avoids interruptions later. -->

| Piece | What it is | When it's used |
|---|---|---|
| `<path to the local copy of the repo>` | Local copy of the repo | Every stage |
| `.claude/settings.local.json` in the local copy (git-ignored) | The token of Claude's GitHub account, and the git author that makes commits show the account. Remove this row if the project has no such account. | Every stage |

## Outside services
Services outside GitHub and Claude that the project depends on. Which ones depends on what kind of software it is:

| Kind of project | Typical outside services |
|---|---|
| Website | Hosting, such as a Claude artifact for something simple, or Cloudflare Pages, Netlify or Vercel. A domain name and its DNS. |
| Web app or API | An app host (Fly.io, Render, a cloud provider). A database (Postgres from Supabase or Neon). Sign-in (Google, GitHub). |
| Mobile app | Apple's App Store Connect and TestFlight. Google Play Console. Push notifications. |
| Command-line tool or code library | A package registry (PyPI, npm, Homebrew). GitHub Releases. |
| Desktop app | GitHub Releases or an app store. Code signing, so the operating system trusts the download. |
| Browser extension | The browser's extension store, such as the Chrome Web Store. |
| Data or machine-learning project | Notebooks (Colab, Jupyter). A place for datasets and models (Hugging Face). |
| Claude plugin | A GitHub repo that works as a plugin marketplace. |

Services many kinds of project use: payments (Stripe), email sending, error tracking (Sentry), AI models through an API, image generators, and accounts Claude can read through connectors (email, calendar, cloud drives, Notion).

<!-- This table is also useful when writing the TDD: each service listed here needs an account, a cost check and a way to test without it. -->

| Piece | What it is | When it's used |
|---|---|---|
| Claude's GitHub account: `<account link>` | The account Claude Code acts as on GitHub, a collaborator on the repo and the board (apb-setup step 5). Its token's expiry: `<date, or never>`. Remove this row if the project has none. | Every stage |
| `<service name and link>` | `<for example: hosting for the live product>` | `<for example: from the first deploy on>` |
