# Lily design guide

The single source of truth for how Lily looks and behaves. It replaces the shared
`~/projects/DESIGN.md` for this app: Lily has its own palette (Mauve), type
(Literata) and layout rules, and nothing outside this file overrides them.

- **Tokens and base components:** `cps/static/css/lily.css`. Every other sheet
  consumes tokens and never defines a colour.
- **Enforcement:** `tests/unit/test_lily_design_static.py` checks the palette,
  contrast, that this file's token tables match `lily.css`, and the
  "never" rules below. A rule that can be tested should be.
- **Changing the language:** edit this file first, then the CSS, then the tests,
  in the same change. When code and this file disagree, the code is wrong,
  unless the disagreement is listed in §12 (Known drift).

---

## 1. Principles

1. **Restraint.** One accent per theme. Flat surfaces, no gradients, no blur,
   no decorative shadows. Colour is spent on attention, not decoration.
2. **Space separates, lines don't.** No horizontal divider lines (`hr` is
   hidden). Separate content with spacing and surface panels. Control
   outlines and focus rings stay. The one exception is the top bar's 1px
   `--line-soft` bottom rule, which marks off the sticky bar from the page.
3. **Controls read by fill, not outline.** No button wears a border. A control
   is recognised by its place, its faint fill and its hover.
4. **One primary per view.** Exactly one solid accent button moves the screen
   forward. Everything else is quiet.
5. **States are tints.** Hover and selection are the theme's own ink or accent
   mixed at a low percentage (§2.3), never grey or white washes.
6. **Quiet motion.** 120 ms ease-out for state changes; anything larger runs
   only under `prefers-reduced-motion: no-preference` (§7).
7. **Both themes are first class.** Every colour token has a light and a dark
   value; no rule reads the colour scheme itself.
8. **One component per job.** Pick a component by what it *is* (§5). Never
   style a control by hand in a template. If nothing fits, add it here and to
   `lily.css` first.

---

## 2. Colour

### 2.1 Themes

| Theme | Selector | Notes |
| --- | --- | --- |
| Light (Mauve) | `:root` | Dusty orchid on warm stone. |
| Dark | `:root[data-theme="dark"]` | Plum-tinted near-black; one block redefines every colour token. |

`lily_theme_head.html` resolves the stored choice (`localStorage["lily-theme"]`)
or `prefers-color-scheme` into a concrete `data-theme` before first paint, so
there is **no** `@media (prefers-color-scheme)` copy of the palette. The
top bar's theme button switches Light ↔ Dark. The `theme-color` metas in
`lily_theme_head.html` must equal `--paper` in each theme.

### 2.2 Palette

The test suite parses this table and compares it with `lily.css`. Keep the
format: one token per row, the light value, then the dark value.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--paper` | `#F1EEEA` | `#1A1517` | Page background, dialogs, fields |
| `--surface` | `#F8F6F3` | `#221B1F` | Raised: panels, menus, popovers, toasts |
| `--sunk` | `#E7E1DC` | `#151012` | Recessed: sidebar, code wells, empty cover slots |
| `--ink` | `#2B2127` | `#F0E8EC` | Primary text |
| `--ink-soft` | `#4A3D45` | `#D6C8CF` | Quiet button labels, resting nav rows, long-form description |
| `--muted` | `#655860` | `#B4A4AC` | Secondary text, help text, icon buttons at rest |
| `--faint` | `#685B62` | `#A89AA2` | Tertiary text, placeholders, counts, empty-state glyphs |
| `--line` | `#CBC1BF` | `#392F35` | Panel edges, menu edges |
| `--line-soft` | `#DAD3D0` | `#2C2428` | Edges inside a panel, cover hairlines |
| `--line-strong` | `#85767C` | `#85757D` | Field and switch edges, empty stars (≥3:1, WCAG 1.4.11) |
| `--accent` | `#854A73` | `#D49BC2` | Primary action, links, focus, chosen state, running work |
| `--heading` | `#854A73` | `#D49BC2` | Every heading (§3) |
| `--success` | `#4F6B4B` | `#9ECE6A` | Done, read, ready |
| `--warning` | `#A13F0E` | `#FF9E64` | Needs attention |
| `--danger` | `#B3261E` | `#F7768E` | Failed (status text and tints) |
| `--on-accent` | `#FFFFFF` | `#1A1517` | Text and marks drawn on an accent fill |

Also defined per theme: `--menu-shadow` (§4.4).

**Contrast rules (tested):** every text token (`ink` … `faint`, `accent`,
`heading`, `success`, `warning`, `danger`) is ≥4.5:1 on `paper`, `surface` and
`sunk` in both themes; `on-accent` on `accent` is ≥4.5:1; `line-strong` is ≥3:1
on all three grounds. Any new token must keep this true.

**Never** write a hex, `rgb()` or `hsl()` value outside the two palette blocks
in `lily.css`. Tints are `color-mix()` of a token (§2.3). The only colour
exceptions are format badges (PDF/EPUB icons keep their own colours) and the
reader's in-book page themes, which are content (§6.7).

### 2.3 Derived states

Same formula in both themes, defined once on `:root`:

| Token | Formula | Use |
| --- | --- | --- |
| `--hover` | accent 10% | List/table row, menu item under the pointer |
| `--selected` | accent 17% | Picked row, chosen chip on hover, checked option |
| `--accent-soft` | accent 12% | Chosen chip, toggle-on button |
| `--control-tint` | ink 6% | Quiet button and chip fill, icon-button hover |
| `--control-tint-strong` | ink 10% | Quiet button hover, pressed icon button, switch off |
| `--row-hover` | ink 5% | Navigation row hover |
| `--row-active` | ink 9% | Current navigation row |

Inline tints use the same pattern: `color-mix(in srgb, var(--tone) N%, transparent)`.
Fixed percentages: **7%** notice fill, **12%** pill fill, **3%** alternate list
row, **30–35%** ink for scrims and modal backdrops.

---

## 3. Typography

One family, **Literata** (self-hosted variable font, weights 200–900), used
through tokens only. **Never** hard-code a `font-family`.

| Token | Value | Use |
| --- | --- | --- |
| `--font-ui` | Literata, Charter, Iowan Old Style, Georgia, serif | Chrome, controls, body |
| `--font-body` | same stack | Headings, reading text, book titles |
| `--font-mono` | SF Mono, ui-monospace, Menlo, monospace | Counts, paths, logs, code |

### 3.1 Headings: two sizes, no more

| Token | Size | Use |
| --- | --- | --- |
| `--title-size` | 22px (20px phone top bar) | Page titles: top bar `.lily-page-title`, settings pane `.lp-heading`, dialog `.modal-title`, login |
| `--heading-size` | 15px | Every other heading: sections, panel titles, table column heads, sidebar and rail groups, menu groups, empty-state titles |

All headings: `--font-body`, weight 600, colour `--heading`, **Title Case**,
never uppercase, no letter-spacing. New heading classes join the grouped
heading rule in `lily.css` (`.lp-label, .lp-heading, …`) rather than restating
the recipe.

Content titles are content, not headings, and keep their own look: the book
title on the book page (40px/700, `text-wrap: balance`) and author names.

### 3.2 Text scale

| Size | Weight | Use |
| --- | --- | --- |
| 11px | 400–500 | Mono counts (nav, chips, list counts), pill and badge labels |
| 12px | 400–600 | Field labels above a control (`.dup-field`), dense meta, logs (mono) |
| 13px | 400 | Help text (`.lp-help`), units, values in link rows, footnotes |
| 14px | 400 | Secondary lines: author under a title, `dt` labels, menu meta |
| **15px** | 400 / 500 | **Base.** Body, buttons, fields, nav rows, row titles (500) |
| 17–18px | 400 | Long-form reading: book description (18), author bio (17) |
| 22px | 600 | `--title-size` |
| display | 600–700 | Content only: book title 40, empty-state glyph 44 |

- Line height: 1.5 body, 1.4 headings and UI rows, 1.6–1.7 long-form reading.
- Weights: 400 text, 500 emphasis and labels, 600 headings and primary
  buttons, 700 only for the book title.
- Numbers in tables, counters and facts use `font-variant-numeric: tabular-nums`.
- On coarse pointers, fields render at 18px so iOS doesn't zoom on focus.

---

## 4. Space, shape and size

### 4.1 Spacing scale

**2 · 4 · 8 · 14 · 22 · 24** (plus 6 for chip gaps).

| Step | Use |
| --- | --- |
| 2 | Between icons in a tight cluster (top-bar actions, view switch) |
| 4 | Label to the thing it labels; focus-ring breathing room |
| 6 | Between chips |
| 8 | Inside a row or control cluster; between buttons |
| 14 | Between rows; heading to its content; panel padding when dense |
| 22 | Between sections and groups; panel padding |
| 24 | Page content margin (16 on phones); dialog padding |

Inner padding of a control (10, 11, 12) is set by its component and not
reused for layout. Don't introduce 15, 18, 20, 26 or 30 for layout gaps (§12).

### 4.2 Radii

| Radius | Use |
| --- | --- |
| 3 | Thumbnails ≤ 60px wide (typeahead, duplicates, list view) |
| 4 | Inline `code`, `pre` |
| 5 | Navigation rows (sidebar, settings rail), draggable order rows |
| **6** (`--control-radius`) | Buttons, chips, icon buttons, menu items, list-view rows |
| 7 | Fields, select toggles, input-group addons |
| 8 | Book covers in the grid, file tiles |
| **10** | Panels, notices, menus, popovers, dialogs, toasts |
| 12 | Grouped settings cards (`.lp-list`), and only those |
| 999 | Pills, badges, switches, progress bars, round play buttons |

The book-page cover is a spine shape: `4px 8px 8px 4px`.

### 4.3 Control sizes

| Control | Size | Label |
| --- | --- | --- |
| Button (any kind) | 30 tall, 14 side padding | 15px, 500 (primary 600) |
| Chip | 30 tall, 14 side padding | 15px, 500; count 11px mono |
| Icon button | 30 × 30, 16px glyph | — |
| Large icon button (top bar, list toolbar) | 38 × 38, 21px glyph | — |
| Book action bar button | 36 tall (44 on coarse pointers), 15 side padding, 14px glyph | 15px |
| Field | 30 tall, 10 inner padding | 15px |
| Switch | 34 × 20, 16px thumb | — |
| Checkbox / radio | 14 × 14, `accent-color` | — |

Bootstrap's `.btn-sm`, `.btn-xs` and `.btn-lg` do not change size. Every
button is one size within its context. On coarse pointers icon buttons get a
44px hit area via `::after`; the drawn size stays the same.

### 4.4 Elevation

Only layers that float above the page cast a shadow, and only
`var(--menu-shadow)`: menus, select pickers, popovers, typeahead, toasts, the
reader settings sheet and the round quick-action buttons on a grid cover. Panels, cards, dialogs and covers are flat (tested).

### 4.5 Breakpoints

Use these widths only (tested). Write `max-width: N` and `min-width: N + 1`.

| Width | Name | What changes |
| --- | --- | --- |
| 600 | Small phone | Dialog and duplicates layouts tighten; reader labels hide |
| 767 / 768 | Phone | Sidebar becomes a drawer; top bar wraps; book grid 2-up; book page stacks; content padding 16 |
| 1099 / 1100 | Tablet | List view drops year and rating; book editor goes two-column |
| 1499 / 1500 | Wide | Book page's facts panel moves from a third column to under the cover |
| 1400, 1700 | Zoom | Whole page zooms 1.1 / 1.2 (`--page-zoom`); size full-height boxes with `calc(100vh / var(--page-zoom))` |

Settings use a container query instead (`@container lp-settings (max-width: 760px)`)
because their width depends on the sidebar. Prefer a container query whenever a
component's room depends on its parent rather than the window.

---

## 5. Components

Bootstrap 3 markup is restyled in `lily.css`, so use the Bootstrap class and
get the Lily component:

| Bootstrap class | Lily component |
| --- | --- |
| `.btn`, `.btn-default` | Quiet button |
| `.btn-primary` | Primary button |
| `.btn-danger` | Destructive confirm button |
| `.form-control`, bare inputs, `select`, `textarea` | Field |
| `.dropdown-menu` | Menu |
| `.modal` | Dialog |
| `.alert-*` | Notice |
| `.label-*`, `.badge` | Pill |
| `.pagination`, `.nav-tabs`, `.nav-pills` | Chip row |
| `.panel`, `.well` | Panel |
| `.table` | Table |
| `.progress` | Progress bar |

### 5.1 Which control?

| You need… | Use |
| --- | --- |
| The one action that moves the view forward | Primary button |
| Any other action with words | Quiet button |
| To confirm something destructive in a dialog | `.btn-danger` |
| A destructive action that isn't the page's job | Quiet button + `.is-danger` |
| An action that is only a glyph | Icon button (`.icon-btn`), with `title` and `aria-label` |
| To narrow a list, pick one of a few, or switch views | Chips (`.btn.lily-chip` via `image.chip()`) |
| A two-state button | Quiet button with `aria-pressed` and a label that stays put |
| A two-state button whose label names the next action ("Mark as read" ↔ "Mark as unread") | Quiet or icon button with `.is-on` and no `aria-pressed`, which would contradict the label |
| To turn a setting on or off | Switch (`lily_form.toggle`) |
| To include or exclude a row | Checkbox |
| To show a small fact | Pill |
| Navigation to a settings sub-page | Link row (`lily_form.link`) |

### 5.2 Buttons

| Kind | Rest | Hover | Pressed / on |
| --- | --- | --- | --- |
| Primary | `--accent` fill, `--on-accent` label, 600 | brightness 1.08 | brightness 0.94 |
| Quiet | `--control-tint`, `--ink-soft`, 500 | `--control-tint-strong`, `--ink` | `aria-pressed="true"` or `.is-on`: `--accent-soft`, `--accent` |
| Destructive confirm (`.btn-danger`) | Same as Primary (accent, not red) | as Primary | as Primary |
| Quiet danger (`.btn.is-danger`, `.icon-btn.is-danger`) | `--control-tint` (icon: no fill), `--accent` label | `--danger` 12%, `--danger` label | — |
| Link (`.btn-link`) | transparent, `--accent` | underline | — |
| Icon (`.icon-btn`) | bare glyph, `--muted`, no fill | `--control-tint`, `--accent` | `aria-pressed`/`.is-on`/`aria-expanded`: `--control-tint-strong`, `--ink` |

- No border in any state (tested). Disabled is `opacity: .4` on the whole
  control, never a colour change.
- Destructive actions rest in the accent, not red: trash icons are
  `.icon-btn.is-danger` in `--accent`. On hover and keyboard focus every
  destructive control (`.is-danger`, row removers, clear-date buttons) turns red:
  `--danger` at 12% behind a `--danger` label, so the pointer warns before the
  click. Otherwise red is for *failure status*.
- A glyph inside a text button is a leading mark in the label's colour.
- Text on any accent fill is `--on-accent`. Never `--paper` or `--surface`.
- Button groups: 8px gap, every button keeps its own radius.

### 5.3 Chips

Separate chips, 6px apart, in a row that scrolls rather than wraps
(`.lily-chips`). There is no segmented control.

| State | Fill | Label |
| --- | --- | --- |
| Rest | `--control-tint` | `--ink-soft`, 500 |
| Hover | `--accent-soft` (chips) / `--control-tint-strong` (tabs, pagination) | `--ink` |
| Chosen (`.active`, `aria-current`) | `--accent-soft` | `--accent` |
| Chosen + hover | `--selected` | `--accent` |

Counts are 11px mono `--faint` and show only when above zero. Under forced
colours the chosen chip gets a 2px `Highlight` outline.

### 5.4 Fields

`--paper` fill, 1px `--line-strong` edge, radius 7, 30 tall (textareas auto).
Placeholder `--faint`. Focus darkens the edge to `--faint` and shows the focus
ring; no glow. Invalid (`.has-error`, `:user-invalid`): edge mixes accent 55%
into `--line-strong`. It warms rather than turning red. Disabled/read-only: `--sunk`
fill at 0.4 opacity. Labels sit 4px above, 15px/500 `--ink-soft`.

Where the browser supports `appearance: base-select`, `select` opens a list
drawn as a Menu (§5.6); elsewhere the native list stays.

### 5.5 Switch and checkbox

- **Switch** (`input[type=checkbox].lp-switch`): track radius 999, 1px
  `--line-strong` edge, off `--control-tint-strong`, on `--accent`; 16px
  `--surface` thumb. Use for settings that take effect on save or at once.
- **Checkbox:** native, 14px, `accent-color: var(--accent)`. Use for picking
  rows or multi-select options (`lily_form.check` inside `.lp-checks`).

### 5.6 Menus, popovers, tooltips

`--surface`, 1px `--line`, radius 10, `--menu-shadow`, 6px inset. Items: padding
7×11, radius 6, `--ink-soft`; hover `--hover`; current `--selected`. Group
heads (`.dropdown-header`) are headings at `--heading-size`. No dividers
(`.divider` is hidden): separate groups with a header. Tooltips use
`--font-ui`; every icon button and truncated text has one, phrased as a plain
sentence with no full stop and the shortcut in brackets: "Hide sidebar (⌘B)".
The exception is a grid cover: no popups over it. The cover, its badges and
its quick-action buttons carry no `title`, only an `aria-label`; the title
printed under the cover already names the book.

### 5.7 Dialogs

`.modal` only, no hand-rolled dialogs. `--paper`, radius 10, no shadow,
backdrop ink at 35%, vertically centred. Padding 24; title at `--title-size`;
one sentence of context; footer right-aligned, 8px gap, Quiet "Cancel" then
the Primary (or `.btn-danger`) last. Escape cancels. An empty title hides the
header.

### 5.8 Notices and toasts

- **Notice** (`.alert-*`): tone at 7% fill, `--ink` text, radius 10, padding
  14, no border. Optional tone icon and `.close`. Links are `--accent` 500. If
  a script shows or hides a notice, toggle `.is-active` and never `.show()`,
  because notices are `display: flex`.
- **Flash messages** all go to the single `#messageContainer` live region
  (`role=status`; danger adds `role=alert`).
- **Toast** (`.lily-refresh-toast`): the only floating message. Bottom-right
  16px, `--surface`, `--line-soft` edge, radius 10, `--menu-shadow`, 14px text,
  tone icon (accent busy, success done, danger error), dismisses itself.

### 5.9 Pills and badges

Small, non-interactive facts: radius 999, padding 3×6, 10–11px/500, tone
label on the same tone at 12% (`.label-success`, `.label-warning`, …); neutral
is ink 12% with `--ink-soft`. If it does something, it's a chip.

### 5.10 Panels

| Kind | Recipe | Use |
| --- | --- | --- |
| **Panel** | `--surface`, 1px `--line`, radius 10, padding 22 (dense: 14), no shadow | Any boxed group of content: `.panel`, `.well`, duplicate cards |
| **Side panel** | `--surface`, no border, radius 10, padding 20×22 | Book page facts (`dl.book-metadata`) |
| **Group card** | ink 2.5% into `--paper`, 1px `--line-soft`, radius 12, padding 0 16 | Settings rows (`.lp-list`) and only that |

A panel nested in a panel drops to a `--line-soft` edge. Never nest more than
one level deep.

### 5.11 Tables and lists

- **Table:** no borders, no zebra. Column heads are headings (`--heading-size`,
  `--heading`). Cells 8×10, tabular numbers. Hover `--hover`, selected
  `--selected`. Sorted column shows ↑/↓ in `--muted`.
- **List row:** radius 6, hover `--hover`, selected `--selected`. Long lists
  (list view) may alternate rows with ink 3%.
- **Navigation row** (sidebar `.lily-nav`, settings rail): min-height 28,
  padding 4×10, radius 5, 15px `--ink-soft`; hover `--row-hover`; current
  `--row-active`, `--ink`, 500, with `aria-current="page"`. Neutral, not
  accent. Counts on the right in 11px mono `--faint`.

### 5.12 Progress and busy

- **Progress bar:** 4px, radius 999, track `--control-tint`, bar `--accent`
  (tone variants for success/warning/danger). On a cover (Continue Reading) it
  runs flush along the bottom edge, 5px, on an ink-22% track (`--control-tint`
  vanishes over cover art); the share read is written under the author
  ("33% read", 14px `--muted`), not on the bar.
- **Busy:** spin the control's own glyph (`.glyphicon-spin`), or `.is-busy`
  (opacity .4) on an icon button. No full-page spinners.

### 5.13 Empty states

`.library-empty-state` is the reference: centred, max-width 560, margin 48
auto; a 44px glyph in `--faint`; a title at `--heading-size`; one sentence in
`--muted`; then the next action(s) as buttons, 8px apart. Invite an action
rather than describe a void ("Add your first book", not "No books").

### 5.14 Ratings

Stars are icons: filled `--accent`, empty `--line-strong`. Sizes: 11px in cards,
12px in list rows, 16px on the book page, 27px in a rating input.

A rating input (`image.rating_input`, `.lily-stars`: the editor and advanced
search) is a radio group: one radio per star (each named "3 stars") and a
"none" option drawn as the trash glyph, which shows once there is a rating to
clear. The radios are out of sight but keep the keyboard (Tab in, arrow keys
to pick); each star label wears its radio's focus ring. Stars up to the chosen
one fill, and hovering previews a rating. It posts "1"…"5", or "" for none.

### 5.15 Book cover

Aspect ratio **1 : 1.414**, `object-fit: cover`, `--sunk` behind, a 1px
`--line-soft` inset hairline (`outline-offset: -1px`), no shadow. Read state is
a 3px `--success` inset outline plus a corner tick badge titled "Finished" on grid
covers, and a green dot in list view. Mark-as-read controls use the tick-in-a-circle
glyph (`glyphicon-ok-circle`), the same mark as the sidebar's Finished row.

---

## 6. Layout and pages

### 6.1 App shell (`layout.html`, `lily-shell.css`)

- **Sidebar:** 232px (`--sidebar-width`), `--sunk`, 1px `--line` on its right
  edge, sticky full height. Groups "Browse" and "Shelves", each headed by a
  `.nav-head` heading. Collapsible on desktop (⌘/Ctrl+B or the drawer toggle,
  remembered); on phones it is an off-canvas drawer with an ink-30% scrim, closed
  by the scrim or Escape, and `visibility: hidden` while closed so its links
  leave the tab order. Opening the drawer moves focus to its first link and
  makes `.lily-main` inert; every way of closing it returns focus to the toggle.
- **Drawer toggle:** a large icon button, first in the top bar at every width.
  `aria-expanded` follows the sidebar (the drawer on phones, the collapse
  wider), and so do its name and tooltip: "Hide sidebar (⌘B)" / "Show sidebar
  (⌘B)". It keeps the resting icon-button look when expanded; the sidebar
  shows its own state.
- **Top bar:** 60px (`--topbar-height`), sticky, `--paper`, no border, padding
  0 24. Order: drawer toggle · page title · `page_title_actions` · search ·
  advanced search · actions (upload, refresh, theme, settings). Actions are
  large icon buttons, 2px apart.
- **Page title lives in the top bar**, not in the content. Content starts with
  sections. The settings pane heading is the one in-content page title. The
  book page is the exception: its book title (§3.1) is the page's `h1`, so the
  top bar renders no title there, not even an empty one.
- **Content:** `main#lily-content`, padding 24 (16 on phones), no max-width.
  A skip link targets it.

### 6.2 Page skeleton

```
top bar title
[toolbar: chips / sort / view switch]       ← margin-bottom 22
## Section heading                           ← --heading-size, margin-bottom 14
content (grid, panel, rows)
                                             ← 22 between sections
```

### 6.3 Library grid and list

- **Grid** (`.lily-grid`): `repeat(auto-fill, minmax(190px, 1fr))`, gap 26;
  phones 2-up, gap 22×14. Card: cover (§5.15), then title 15/500 clamped to
  two lines, then meta 14px `--muted`. Quick actions are round buttons
  in a row at the cover's bottom right, 8px in, 6 apart: 32px `--surface`
  discs with a 1px `--line` edge and `--menu-shadow`, `--ink` icons, `--accent` on hover. They rise and fade in
  on hover/focus and stay visible on touch (36px, 8 apart). Read state fills the tick's disc
  `--success` with a `--surface` tick. No popups over the cover or its buttons (§5.6).
- **Series grid** (`grid.html`): Isotope lays it out with fixed 160px tiles,
  22 apart, so it doesn't follow the 190/26 card grid.
- **List view ("ledger"):** one shared `--ledger-cols` track list for header and
  rows; rows radius 6, alternate ink 3%, hover `--hover`; read state is a dot.
- **Browse lists** (`list.html`: categories, authors, publishers…): one
  `.lily-list` flowed into 300px CSS columns, gap 22, so each count sits
  beside its name; lists of 12 or fewer stay one column, max 560. Rows are
  list rows (§5.11), min-height 40; long names wrap.
- **Continue Reading** (`.continue-reading-row`): one row that scrolls sideways,
  never wraps, so the library starts on the first screen. Covers are 140px wide
  (112 on phones), 22 apart (14 on phones). The cover opens the reader in a
  new tab at the saved format; the title opens the book page.
- **Toolbar** (`.lily-list-toolbar`): chips and sort on the left, view switch
  (large icon buttons) top-right, margin-bottom 22. Toolbar chips are 38 tall
  so they sit level with the view switch. The direction chip shows only its
  arrow (38 square, the word kept for screen readers and the tooltip naming
  the next order).

### 6.4 Book page ("Shelf" layout)

- **≥1500px:** three columns: cover | heading and actions | facts side panel
  (§5.10). The description sits under the cover and runs across the cover and
  middle columns, stopping at the panel. Heading and action rows are
  `min-content` and a `1fr` row takes the cover's extra height.
- **768–1499px:** two columns; the facts panel stacks under the cover and the
  description sits under the actions in the second column.
- **≤767px:** the cover becomes a 108px thumbnail beside the title; actions,
  description and facts go full-width, facts last. Reset row sizing here. The
  primary action takes a full line; the rest share the next.
- The Primary reads "Continue · 33%" for a book in progress (the share at 500)
  and opens the reader in a new tab at the format last read.
- Description: `--font-body` 18px, line-height 1.68, max 78ch, `--ink-soft`.
- Every fact is one line; a long value ends in an ellipsis, never wraps.
- Shelves and tags are plain rows in the facts panel: names as comma-separated
  `--accent` links, no chip or icon.
- Papers: an arXiv row shows the id itself, linked to the abstract page; its
  DOI isn't shown. Other identifiers (a non-arXiv paper's DOI included) stay as
  named links in one Identifiers row. A Citations row fills in after load from OpenAlex
  and stays hidden when the paper isn't found.
- **Editor** (`book_edit.html`): Title, authors, tags, shelves and description
  always show. Series, publisher, published date, language and rating show only
  when the book has a value; the rest wait behind small "Add …" buttons at the
  end of Details, and Fetch Metadata reveals any field it fills.
  The description box fits its text (no drag handle), padding 14/16 and
  line-height 1.68 like the book page.
  The Save panel starts with a secondary Read (new tab) when the book has a
  readable format; Save stays the one Primary.
- **Fetch Metadata results** are compact cards: a 128px cover column with
  Apply under it, fields in 14px, a description clamped to six lines. The match
  score is a bare 24px number ("21%") in the card's top right; an exact match
  shows its pill under Apply instead.

### 6.5 Settings (`settings_layout.html`, `lily_form.html`)

Build every settings page from the macros. Never hand-write rows.

| Macro | Produces |
| --- | --- |
| `f.group(label, help, id, actions)` | Section heading + help + a Group card |
| `f.row(label, for_id, help, wide)` | A label/help column and a right-aligned control column |
| `f.toggle(name, label, checked, help)` | Row with a switch |
| `f.input(…)` / `f.select(…)` | Row with a field |
| `f.check(…)` inside `.lp-checks` | Checkbox grid |
| `f.link(href, label, help, value)` | Navigational row with value and chevron |
| `f.sub(related)` | Dependent rows, indented 22, shown when their control is on |

- Frame: rail (180px) | pane, gap 40. Under 760px of container width the rail
  becomes a scrolling chip strip.
- Rows: min-height 52, padding 11 0, label 15/500 `--ink`, help 13px `--muted`
  (max 52ch). **No hairlines between rows.**
- Save bar `.lp-actions.is-save`: sticky to the bottom on `--paper`, Primary
  last on the right; hidden until the form is dirty (visible without JS).
- A form page outside the frame (the shelf editor) is the same groups and
  bar, capped at 640px. A short field like a name is a wide row, label
  above the field. Its quiet `.is-danger` delete sits at the left of the bar,
  Cancel and the Primary at the right.

### 6.6 Login and standalone pages

Login (`login.css`) is a two-plate layout and the only page without the shell.
It has exactly one Primary button and no inline styles (tested). Error pages
use `.lily-standalone` with max-width 560.

### 6.7 Reader

Every way into the reader (Read/Continue, a cover's read button, Continue
Reading) opens it in a new tab, so the library stays where it was.

`lily-reader.css` styles the reader **chrome** (title bar, sidebar, settings
sheet, audio player) with tokens. The page zoom is reset to 1, and the chrome
alone takes the site's wide-screen zoom through `--reader-zoom` (same
breakpoints as §3), so controls match the rest of the site. The epub `#viewer`
stays unzoomed because epub.js sizes its iframe from it; its insets are
multiplied by `--reader-zoom` by hand. The book *page* themes (Light, Sepia,
Dark, Black in `main.css`) are content and keep their own hex values. The PDF
reader (pdf.js `viewer.css`) is outside the system; `lily-pdf.css` only zooms
its toolbars (1.25× base, times the site zoom, from 1100px) to the site's
control size and keeps the pages unzoomed. Its toolbar starts with a "Back to
book" link (`#backToBook`, a pdf.js `toolbarButton` with its own chevron), and
at 600px and below PDFs open at page width instead of 150%.
The DjVu reader uses the epub title bar (`#titlebar`: Back, title, controls),
the pdf reader's page box ("3 of 120") and zoom list as Fields, and the epub
side arrows in 88/56px gutters. The vendored viewer's own toolbar and status
sprite are hidden; `djvu_reader.js` drives them and paints the canvas backdrop
with `--sunk`, so the bar and page sit on one surface in both themes. Books open
at Fit page (Fit width at 600px and below), where the arrows move into the bar
and the page runs edge to edge. A file that is missing or isn't a DjVu shows a
`.reader-error` panel instead of loading for ever. Like PDFs, the position is
synced as `page:N`.
Reader title bars are 56px. Their buttons are large icon buttons (§4.3). The
EPUB title bar and page arrows take the page theme's ink (set inline from
`window.themes`, a content colour like the page background), so they tint with
`currentColor` (16% when pressed or expanded) instead of `--control-tint`.
Sidebar tabs and settings options are chips with `aria-pressed`.
The EPUB page theme follows the app's Light/Dark choice (Light → Light, Dark →
Dark) until one is picked in the reader's settings; only a pick is saved
(`localStorage["lily-reader-theme"]`).
Bookmarks: any number per book, kept per user on the server
(`/ajax/bookmarks/<id>/<FORMAT>`, keyed like the position sync: a CFI or
`page:N`; `bookmarks.js`). Every reader's bookmark button marks the page on
screen and is pressed (`aria-pressed`) on a bookmarked page. EPUB lists them
in the sidebar's Bookmarks tab in book order, each the chapter (a heading)
over the page's first words, with a `.icon-btn.is-danger` trash button. DjVu
adds a list button (`aria-expanded`) that opens a Menu (§5.6) of pages; the
PDF toolbar gets the same two as pdf.js `toolbarButton`s (pressed is
`.toggled`) and the list as a doorhanger beside Tools. A failed bookmark
request says so in one quiet line at the foot of the page (`#bookmark-status`,
`role=status`), never in an `alert()`.

---

## 7. Motion

| Kind | Duration / easing | Where |
| --- | --- | --- |
| State change (hover, focus, colour) | 120 ms `ease-out` | Every control |
| Entrance | 600 ms `cubic-bezier(.25,.46,.45,.94)`, staggered 30 ms (cap 14) | Book cards |
| Hover lift | 300 ms, `translateY(-5px)` | Grid covers |
| Toast | 180 ms in, 200 ms out | `.lily-refresh-toast` |
| Drawer slide | 120 ms `ease-out` | App drawer, reader sidebar |
| Cover actions rise | 300 ms, same curve as the hover lift | Grid quick actions |
| Search widen | 400 ms, entrance curve | Top bar search on focus |
| Refresh spin | 800 ms once on hover | `#refresh-library` |
| Read-mark draw | 450 ms, entrance curve | A tick just marked read |

Anything beyond a state change goes inside
`@media (prefers-reduced-motion: no-preference)`. The global
`prefers-reduced-motion: reduce` rule in `lily.css` stops all transitions and
animations. Nothing loops unless work is running. Bootstrap's fade/slide
transitions are turned off in `lily.js`.

---

## 8. Icons

- Phosphor Regular, drawn as CSS masks over Bootstrap's Glyphicon class names:
  `<span class="glyphicon glyphicon-cog" aria-hidden="true"></span>`. Colour is
  `currentColor`; size is `font-size`.
- `lily-icons.css` is **generated**: never edit it. To add an icon, copy
  the SVG from `@phosphor-icons/core/assets/regular` into
  `cps/static/icons/phosphor/`, map it in `ICONS` in `scripts/build_icons.py`,
  and run `python3 scripts/build_icons.py`.
- Sizes: 14 inline and nav · 16 icon button · 21 large icon button · 44 empty state.
- Decorative icons are `aria-hidden="true"`; an icon-only control carries the
  label (`aria-label` + `title`).

---

## 9. Accessibility

- **Focus:** `:focus-visible` ring, 2px solid accent at 70%, offset 2 (−2 inside
  containers that clip). Never remove it without replacing it.
- **Forced colours:** buttons, icon buttons and `.close` get a 1px `ButtonText`
  edge; chosen chips a 2px `Highlight` outline. Icons opt out of colour
  adjustment.
- **Contrast:** §2.2 rules, enforced by tests.
- **Touch:** 44px minimum hit area on coarse pointers. Nothing clips that
  area: a control that hides a native input inside it (the upload button)
  spreads the input over the whole 44px.
- **Hidden native inputs** (the upload button's file input, a rating's radios)
  keep the keyboard, and the control drawn for them wears their focus ring
  (`:has(input:focus-visible)`, or `input:focus-visible + label`).
- **Structure:** one skip link to `#lily-content`; one live region for
  messages; current navigation marked with `aria-current="page"`; toggles use
  `aria-pressed`, unless their label flips to name the next action (then
  `.is-on`, §5.1); menus `aria-expanded`. Repeated controls name their object:
  a grid cover's quick actions read "Read Quiet Machines", "Mark Quiet
  Machines as read", "Edit Quiet Machines".
- **Floating messages wait:** a toast that dismisses itself holds while the
  pointer or keyboard focus is on it.
- **Hidden things leave the tab order** (`visibility: hidden` or `hidden`),
  never just off-screen.

---

## 10. Voice

- **Title Case** for every title: pages, sections, dialogs, groups, table
  heads ("Import & Metadata", "Replace Cover"). Short words (a, an, and, of,
  on, to, the, in, from) stay lowercase unless first.
- **Sentence case** for everything else: buttons, labels, help, menu items.
- Buttons name what happens: "Save", "Scan folders", "Delete 3 books". Never "OK".
- Errors say what broke and what to do next. Help text is one sentence and
  wraps at about 52–62ch.
- Empty states invite the next action.

---

## 11. Working on the UI

### 11.1 Where things go

| File | Holds |
| --- | --- |
| `lily.css` | Tokens, element defaults, every shared component. The **only** place a colour value is written. |
| `lily-shell.css` | Sidebar, top bar, flashes, toast, drawer |
| `lily-library.css` | Grid, list view, toolbar, book page, editor, search, pickers |
| `lily-admin.css` | Settings frame (`.lp-*`), admin pages, logs, error page |
| `lily-stats.css` | Duplicates page (the name predates the stats removal; rename to `lily-duplicates.css`) |
| `lily-reader.css` | Reader chrome (loaded only by reader templates) |
| `lily-pdf.css` | pdf.js toolbar sizing (loaded only by `readpdf.html`) |
| `lily-icons.css` | Generated icons |
| `login.css` | Login and change-password |
| `style.css`, `upload.css`, `lily-fixes.css` | Legacy. Don't add to them; move rules out when you touch them |

Load order: Bootstrap → page libraries → legacy sheets → `lily.css` → page
sheets → icons. Page sheets scope by wrapper class and consume tokens only.

### 11.2 Before adding CSS

1. Is there a component in §5 for this? Use its class or macro.
2. Is it the same as something on another page? Promote it to `lily.css`
   instead of copying it.
3. Every value is a token or on a scale here: colour (§2), size (§3.2),
   spacing (§4.1), radius (§4.2), breakpoint (§4.5).
4. Light and dark both checked; keyboard focus visible; reduced motion
   respected.
5. New hard rule? Add it to this file and a static test.

### 11.3 Never

- Hex/rgb/hsl outside the palette blocks · a hard-coded `font-family`
- Gradients, blur, `backdrop-filter`, shadows on non-floating layers
- Borders on buttons · horizontal divider lines (except the top bar's rule) · uppercase text
- A third heading size · a heading not in `--heading`
- Inline `style=""` for appearance · `!important` in new rules
- A new breakpoint outside §4.5 · editing `lily-icons.css` by hand
- A second Primary button in a view

### 11.4 Verify

```sh
.venv/bin/python -m pytest tests/unit/test_lily_library_static.py tests/unit/test_lily_design_static.py \
  tests/unit/test_lily_admin_static.py tests/unit/test_lily_stats_static.py tests/unit/test_lily_reader_static.py
git diff --check
```

Then look at the changed page in both themes and at phone width.

---

## 12. Known drift

Places where the code doesn't yet match this guide. Fix toward the guide and
delete the line. Don't copy any of these.

**Duplicated components (promote to `lily.css`)**
- Dialog skins: reader `.md-content` (the vendored `reader.min.js` drives it
  with `md-show`, and the reader has no Bootstrap), `#metaModal` in `style.css`.
  Target `.modal` (§5.7).
- Tone pill: `.duplicate-count`. Target `.label-*` (§5.9).
- 30px square remove glyph buttons in the library editor, search form and
  duplicates. Target `.icon-btn`.
- Field-over-label columns: `.dup-field`.
- Focus ring rewritten by hand on `.bootstrap-select` (it has to beat the
  library's `!important`).

**Legacy**
- `style.css` still holds dead rules (`.cwa_stats_*`, old book card,
  `.stats_see_more_btn`). Delete them.
