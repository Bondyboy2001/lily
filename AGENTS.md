# UI preferences

- `docs/design.md` is the design guide: tokens, type, spacing, components, page layouts and the "never" list. Read it before any UI change. Use a component it defines rather than styling by hand. If the language changes, update the guide, `lily.css` and the static tests in the same change. Don't copy anything listed under "Known drift"; fix it toward the guide when you touch it.
- Headings use two sizes only: `--title-size` for page titles (top bar, modals, login) and `--heading-size` for every other heading (sections, table columns, sidebar groups, menu groups, empty states). All are title case, in `--heading`, never uppercase. Book titles are content and keep their own look. Use Literata through the `--font-*` tokens; never hard-code a font.
- Do not add horizontal divider lines (the top bar's bottom rule is the one exception). Use spacing and surface panels to separate content. Keep normal text wrapping, control outlines, and accessible focus indicators.
- Book page uses the "Shelf" layout: cover | title, actions and description | metadata as a compact surface side panel. Below 1500px the panel stacks under the cover; on phones the cover is a thumbnail beside the title and the panel sits last. Keep the heading and action grid rows content-sized so tall covers do not push the description down. Reset row sizing in the mobile layout.

# Verification

- Run shared UI checks with `.venv/bin/python -m pytest tests/unit/test_lily_library_static.py tests/unit/test_lily_design_static.py tests/unit/test_lily_admin_static.py tests/unit/test_lily_stats_static.py tests/unit/test_lily_reader_static.py`.
- Run `git diff --check` before finishing changes.

# Git and releases

- Work on `main`. Don't start long-lived branches; if a change needs one, merge it back the same day.
- When a change is finished and its checks pass, commit it and push to `main` straight away. Stage only the files you changed (`git add <paths>`, never `git add -A`), since other sessions may be editing the same tree. Don't leave finished work uncommitted.
- Every push to `main` runs `.github/workflows/release.yml`: ruff, mypy, unit tests and a container `/health` check, then it publishes `coldestpillow/lily:latest` (and GHCR). A failing check publishes nothing, so keep `main` green.
- When running several sessions in parallel, give each its own worktree (`claude --worktree`), then merge to `main` and delete the worktree when done.
