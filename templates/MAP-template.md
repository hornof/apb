# `<project>`: project map

Every piece of this project, where it lives, and when it's used. Claude adds each new piece as soon as it exists.

**Now:** `<current stage, for example "PRD in review">`

**Stages:** brain dump → PRD (gate 1) → TDD (gate 2) → board → build → review and merge (gate 3) → check the live product

## GitHub
| Piece | What it is | When it's used |
|---|---|---|
| Repo: `<repo link>` | The code, docs and content | Every stage |
| Board: `<board link>` | Progress, one card per piece of work | From the board stage on |
| Brain dump: `<issue link>` | The original description of the project | Start |

## Claude pages
Pages Claude makes and hosts. Each one is private to its creator until shared. Kinds of pages and how a project can use them:

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
| `<page name and link>` | `<for example: clickable mockups of the main screens>` | `<for example: PRD review>` |

## Local files
Files on the developer's computer. Claude can read and write a folder only after the developer gives it access. Kinds of local files a project can use:

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

## Outside services
Services outside GitHub and Claude that the project depends on. Which ones depends on what kind of software it is:

| Kind of project | Typical outside services |
|---|---|
| Website | Hosting, such as a Claude page for something simple, or Cloudflare Pages, Netlify or Vercel. A domain name and its DNS. |
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
| `<service name and link>` | `<for example: hosting for the live product>` | `<for example: from the first deploy on>` |
