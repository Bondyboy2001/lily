"""Static checks that Lily's styling follows ~/projects/DESIGN.md (Lily palette)."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
TEMPLATES = REPO_ROOT / "cps/templates"

LILY_PALETTE = {
    "paper": "#FDFCFA", "surface": "#FFFFFF", "sunk": "#F4EFE7",
    "ink": "#2B2326", "ink-soft": "#473C40", "muted": "#62575B", "faint": "#6B6064",
    "line": "#C2B3A6", "line-soft": "#D6CABE",
    "accent": "#9E2F55", "heading": "#9E2F55",
    "success": "#4F6B4B", "warning": "#A13F0E", "danger": "#B3261E",
}
TEXT_TOKENS = ["ink", "ink-soft", "muted", "faint", "accent", "heading", "success", "warning", "danger"]
LILY_STYLESHEETS = ["lily.css"]


def read(path):
    return path.read_text(encoding="utf-8")


def css_rules(css):
    """(selector, body) for every innermost rule; @media wrappers are skipped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def root_tokens():
    tokens = {}
    for selector, body in css_rules(read(CSS / "lily.css")):
        if selector == ":root":
            for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body):
                tokens[name] = value.strip()
    return tokens


def luminance(hex_colour):
    channels = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_lily_css_defines_the_lily_palette():
    tokens = root_tokens()
    for name, value in LILY_PALETTE.items():
        assert tokens.get(name, "").upper() == value, name


def test_text_tokens_meet_contrast_on_paper_and_sunk():
    for name in TEXT_TOKENS:
        for ground in ("paper", "sunk"):
            ratio = contrast(LILY_PALETTE[name], LILY_PALETTE[ground])
            assert ratio >= 4.5, (name, ground, round(ratio, 2))
    assert contrast(LILY_PALETTE["paper"], LILY_PALETTE["accent"]) >= 4.5


def test_derived_states_use_guide_formulas():
    tokens = root_tokens()
    assert tokens["hover"] == "color-mix(in srgb, var(--accent) 10%, transparent)"
    assert tokens["selected"] == "color-mix(in srgb, var(--accent) 17%, transparent)"
    assert tokens["accent-soft"] == "color-mix(in srgb, var(--accent) 12%, transparent)"
    assert tokens["control-tint"] == "color-mix(in srgb, var(--ink) 6%, transparent)"
    assert tokens["control-tint-strong"] == "color-mix(in srgb, var(--ink) 10%, transparent)"
    assert tokens["row-hover"] == "color-mix(in srgb, var(--ink) 5%, transparent)"
    assert tokens["row-active"] == "color-mix(in srgb, var(--ink) 9%, transparent)"
    assert tokens["control-radius"] == "8px"


def test_no_gradients_or_blur():
    for name in LILY_STYLESHEETS:
        css = read(CSS / name)
        for banned in ("gradient", "backdrop-filter", "blur("):
            assert banned not in css, (name, banned)


def test_shadows_only_on_menus_and_popovers():
    for name in LILY_STYLESHEETS:
        for selector, body in css_rules(read(CSS / name)):
            for value in re.findall(r"box-shadow\s*:\s*([^;]+)", body):
                if value.strip() not in ("none", "none !important"):
                    assert "dropdown-menu" in selector or "popover" in selector, (name, selector)


def test_buttons_have_no_border():
    rules = dict(css_rules(read(CSS / "lily.css")))
    assert re.search(r"border\s*:\s*0\b", rules[".btn"])
    assert re.search(r"border-radius\s*:\s*var\(--control-radius\)", rules[".btn"])


LILY_STYLESHEETS.append("lily-shell.css")

KEPT_IDS = [
    "query", "query_submit", "advanced_search", "form-upload", "btn-upload", "btn-upload2",
    "top_tasks", "top_admin", "refresh-library", "top_user", "logout", "login", "register",
    "scnd-nav", "nav_createshelf", "duplicate-count-badge",
    "message_library_refresh", "library_refresh_message", "loader", "bookDetailsModal",
]


def test_layout_loads_lily_styles_and_not_caliblur_css():
    layout = read(TEMPLATES / "layout.html")
    for asset in ("css/lily.css", "css/lily-shell.css"):
        assert asset in layout, asset
    for asset in ("caliBlur.css", "caliBlur_override.css", "lily-light.css"):
        assert asset not in layout, asset
    assert layout.index("css/cwa.css") < layout.index("css/lily.css") < layout.index("css/lily-shell.css")


def test_legacy_caliblur_assets_are_deleted():
    for path in ("css/caliBlur.css", "css/caliBlur_override.css", "css/lily-light.css",
                 "js/caliBlur.js", "css/images/caliblur"):
        assert not (REPO_ROOT / "cps/static" / path).exists(), path


def test_theme_switching_is_gone():
    layout = read(TEMPLATES / "layout.html")
    main_js = read(REPO_ROOT / "cps/static/js/main.js")
    for needle in ("lily-theme", "cwa-switch-theme", "data-theme", "current_theme", "allow-mobile-blur"):
        assert needle not in layout, needle
    for needle in ("lily-theme", "cwa-switch-theme"):
        assert needle not in main_js, needle
    assert '<meta name="theme-color" content="#FDFCFA">' in layout


def test_layout_keeps_hooks_other_scripts_use():
    layout = read(TEMPLATES / "layout.html")
    assert 'class="navbar lily-topbar"' in layout
    for element_id in KEPT_IDS:
        assert f'id="{element_id}"' in layout, element_id


def test_profile_menu_guards_anonymous_users():
    layout = read(TEMPLATES / "layout.html")
    menu = layout[layout.index('class="dropdown-menu dropdown-menu-right lily-profile-menu"'):]
    menu = menu[:menu.index("</ul>")]
    assert "{% if current_user.is_anonymous %}" in menu
    assert menu.index("{% if current_user.is_anonymous %}") < menu.index('id="login"')
    assert "{% if not current_user.is_anonymous %}" in menu
    assert menu.index("{% if not current_user.is_anonymous %}") < menu.index('id="logout"')


def test_layout_loads_lily_js_after_main_js():
    layout = read(TEMPLATES / "layout.html")
    assert "js/lily.js" in layout
    assert layout.index("js/main.js") < layout.index("js/lily.js")
    js = read(REPO_ROOT / "cps/static/js/lily.js")
    for needle in ("drawer-open", "aria-expanded", "Escape", ".lily-scrim"):
        assert needle in js, needle


LILY_STYLESHEETS.append("login.css")
AUTH_TEMPLATES = ["login.html", "register.html"]


def test_auth_templates_have_one_primary_and_no_inline_styles():
    for name in AUTH_TEMPLATES:
        html = read(TEMPLATES / name)
        assert html.count("btn-primary") == 1, name
        assert 'style="' not in html, name


def test_login_css_uses_tokens_only():
    css = re.sub(r"/\*.*?\*/", "", read(CSS / "login.css"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "rgba(" not in css and "hsla(" not in css
    assert "!important" not in css


def test_sidebar_nav_avoids_legacy_navigation_rules():
    # style.css still styles `.navigation .create-shelf` / `.nav-head` (teal button, grey rules).
    layout = read(TEMPLATES / "layout.html")
    assert 'class="navigation"' not in layout


def test_flash_rows_reset_legacy_negative_margin():
    # An older rule gives .row-fluid a -20px top margin, pulling notices under the sticky top bar.
    bodies = [b for s, b in css_rules(read(CSS / "lily-shell.css")) if s == ".lily-topbar ~ .row-fluid"]
    assert any(re.search(r"(^|;)\s*margin\s*:\s*0\s*;", b) for b in bodies)


def test_login_flower_rules_outrank_container_img_rule():
    # style.css `.container-fluid img { height: auto }` (0,1,1) beats a bare `.lily-plate-flower`.
    selectors = [s for s, _ in css_rules(read(CSS / "login.css")) if "lily-plate-flower" in s]
    assert selectors and all(s == ".lily-login .lily-plate-flower" for s in selectors), selectors


def test_hidden_flash_rows_add_no_space():
    # The always-present, hidden #message_library_refresh row must not pad the page.
    rules = css_rules(read(CSS / "lily-shell.css"))
    rows = [b for s, b in rules if s == ".lily-topbar ~ .row-fluid"]
    assert rows and not any(re.search(r"padding\s*:\s*14px", b) for b in rows)
    alerts = [b for s, b in rules if s == ".lily-topbar ~ .row-fluid > .alert"]
    assert alerts and re.search(r"margin\s*:\s*14px 0 0", alerts[0])


def test_hidden_sidebar_links_leave_the_tab_order():
    rules = css_rules(read(CSS / "lily-shell.css"))
    assert any(s == ".lily-sidebar" and "visibility: hidden" in b for s, b in rules)
    assert any(s == ".lily-app.drawer-open .lily-sidebar" and "visibility: visible" in b for s, b in rules)
    login = css_rules(read(CSS / "login.css"))
    assert any("body.login .lily-sidebar" in s and "display: none" in b for s, b in login)
