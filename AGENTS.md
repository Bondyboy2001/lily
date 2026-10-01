# UI preferences

- Headings use two sizes only: `--title-size` for page titles (top bar, modals, login) and `--heading-size` for every other heading (sections, table columns, sidebar groups, menu groups, empty states). All are title case, in `--heading`, never uppercase. Book titles are content and keep their own look. Use Literata through the `--font-*` tokens; never hard-code a font.
- Do not add horizontal divider lines. Use spacing and surface panels to separate content. Keep normal text wrapping, control outlines, and accessible focus indicators.
- Book page uses the "Shelf" layout: cover | title, actions and description | metadata as a compact surface side panel. Below 1500px the panel stacks under the cover; on phones the cover is a thumbnail beside the title and the panel sits last. Keep the heading and action grid rows content-sized so tall covers do not push the description down. Reset row sizing in the mobile layout.

# Verification

- Run shared UI checks with `.venv/bin/python -m pytest tests/unit/test_lily_library_static.py tests/unit/test_lily_design_static.py tests/unit/test_lily_admin_static.py tests/unit/test_lily_stats_static.py tests/unit/test_lily_reader_static.py`.
- Run `git diff --check` before finishing changes.
