---
name: apb-review
description: Run the reviewer role on a PR or on the deployed site. Must run in a session that did not write the code under review.
---
Launch the plugin's `reviewer` agent to do the review — it runs in its own context, separate from whatever built the code. Never review in-session work yourself. The reviewer posts its review in its role's format (see `agents/reviewer.md`: "For the builder" blockers and "For the human" notes), each finding tied to a file/line or a measured observation of the live site, ending with an explicit verdict: merge-ready, or the numbered blockers.
