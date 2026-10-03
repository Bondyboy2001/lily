# UI preferences

- `docs/design.md` is the design guide: tokens, type, spacing, components, page layouts and the "never" list. Read it before any UI change. Use a component it defines rather than styling by hand. If the language changes, update the guide, `lily.css` and the static tests in the same change. Don't copy anything listed under "Known drift"; fix it toward the guide when you touch it.
- Headings use two sizes only: `--title-size` for page titles (top bar, modals, login) and `--heading-size` for every other heading (sections, table columns, sidebar groups, menu groups, empty states). All are title case, in `--heading`, never uppercase. Book titles are content and keep their own look. Use Literata through the `--font-*` tokens; never hard-code a font.
- Do not add horizontal divider lines (the top bar's bottom rule is the one exception). Use spacing and surface panels to separate content. Keep normal text wrapping, control outlines, and accessible focus indicators.
- The book page uses the "Frontispiece" layout; design §6.4 is the source of truth for it. Update §6.4, not this file, when it changes.

# Product decisions

Lily is a single-user home library read in the web reader. These are settled; don't reverse them without being asked.

- Removed for good, routes included: Kobo/KOReader sync, email and send-to-eReader, LDAP/OAuth/proxy/magic-link login, public registration, Google Drive, 2FA and API tokens, statistics, archive, Convert to EPUB and any format conversion, Kepub, the auto-zipper, translations (English only).
- Offline reading is removed for good (2026-10-03): no service worker, no Save offline button, no Offline page.
- Tags are the user's own: lookups and imports never add them, and the sidebar has no Tags entry.
- No publishers, languages or ratings anywhere (2026-10-03): not in the editor, book page, cards, search, sidebar, OPDS or lookups; imports clear them. The published date stays.
- Shelves: a book's shelves change from the book page's Shelves menu or the edit page's Shelves rows. Shelf-page covers have no remove button.
- Settings are Profile / Metadata / Users / Duplicates / Logs. Don't add pages for options the user will never touch.

# Verification

- Quick UI check for template/CSS changes: `.venv/bin/python -m pytest tests/unit/test_lily_library_static.py tests/unit/test_lily_design_static.py tests/unit/test_lily_admin_static.py tests/unit/test_lily_duplicates_static.py tests/unit/test_lily_reader_static.py`.
- Before pushing anything else, run what CI runs: `scripts/check.sh` (ruff, vulture and mypy at CI's pins, a few seconds; for vulture, delete dead code, don't whitelist it), then `PYTHONPATH=.:scripts .venv/bin/python -m pytest -m "smoke or unit" -n auto`.
- The `.githooks/pre-push` hook runs `scripts/check.sh` on the exact commit being pushed and refuses the push if it fails. It is on when `git config core.hooksPath` prints `.githooks` (set it once per clone; worktrees share it). Don't bypass it with `--no-verify`; fix the code.
- Don't pipe pytest into `tail`/`head` to judge the result; read the summary line and any `FAILED` lines.
- Run `git diff --check` before finishing changes.
- After a change, rebuild the local container (`scripts/deploy-local.sh`, :8083) and look at the page. From a worktree, copy `docker-compose.local.yml` in (and delete it after) and run `docker-compose -p lily -f docker-compose.local.yml up -d --build --force-recreate`.

# Git and releases

- The main checkout is shared by parallel sessions and is often dirty with their unfinished work. Make each change in your own worktree off `origin/main`, inside the repo's ignored `.claude/worktrees/` folder (`claude --worktree`, or `git worktree add .claude/worktrees/<topic> origin/main -b <topic>`), never as a sibling folder next to `lily/`. Push it, then remove the worktree and its branch.
- Don't start long-lived branches; merge back the same day.
- When a change is finished and its checks pass, commit it and push to `main` straight away. Stage only the files you changed (`git add <paths>`, never `git add -A`). Don't leave finished work uncommitted.
- If the push is rejected, `git pull --rebase origin main`, re-run the checks touching what changed, and push again. Never force-push `main`.
- Other sessions commit between your steps: make fix-ups new commits, not `--amend`, and don't use `git stash` in the shared checkout.
- Commit messages: `type(area): a plain sentence of what the user now sees`, e.g. `feat(book): a Shelves menu on the book page puts the book on a shelf or takes it off`. Types: feat, fix, chore, docs, refactor, test.
- Every push to `main` runs `.github/workflows/release.yml`: ruff, mypy, unit tests and a container `/health` check, then it publishes `coldestpillow/lily:latest` (and GHCR), which the NAS pulls. A failing check publishes nothing, so keep `main` green. Vulture runs as its own job: dead code fails the run but doesn't hold back the image. Docs-only pushes don't trigger it.
- After pushing, watch your release to the end: `gh run watch $(gh run list -w release.yml -c $(git rev-parse HEAD) -L 1 --json databaseId -q '.[0].databaseId') --exit-status`. If it fails, fixing `main` comes before anything else, even if another session's commit broke it. A failed run on `main` opens a "Release failing" issue (label `release-failing`), and the next green run closes it.
- Tag `vX.Y.Z` on `main` after a good batch of changes; the release adds `:X.Y.Z` and `:X.Y` image tags to roll back to.
