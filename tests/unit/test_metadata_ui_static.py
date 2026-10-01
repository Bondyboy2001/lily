from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_metadata_description_apply_syncs_the_description_editor():
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")

    # The description box is a plain textarea now, not TinyMCE, so Apply sets its
    # value and fires lily:set-html for edit_books.js to pick up.
    assert '$("#comments").val(book.description || "").trigger("lily:set-html");' in js


def test_description_editor_listens_for_lily_set_html():
    js = (REPO_ROOT / "cps/static/js/edit_books.js").read_text(encoding="utf-8")

    assert '$box.on("lily:set-html", function () {' in js


def test_metadata_result_button_is_apply_not_save():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")

    assert 'class="btn btn-default meta-apply"' in template
    assert '>{{_("Apply")}}</button>' in template
    # Apply is the only action: no Fill form or Tick all / none
    assert "meta-fill" not in template and "meta-toggle-all" not in template


def test_metadata_source_links_to_the_result_itself():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")

    # An arXiv result links to its abstract page, not arxiv.org
    assert 'href="<%- safeUrl(book.url) || safeUrl(book.source.link) %>"' in template


def test_match_score_is_a_big_number_in_the_cards_top_right():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")
    css = (REPO_ROOT / "cps/static/css/lily-library.css").read_text(encoding="utf-8")

    # Right after the cover column closes, as the card's own corner badge
    assert ('    </div>\n    <% if (!book.exact_match && book.score) { %>\n'
            '    <div class="meta-score"') in template
    assert "<%- Math.round(book.score * 100) %>%</div>" in template
    assert "#meta-info #book-list .media .meta-score {\n    position: absolute;" in css


def test_fetch_metadata_has_no_provider_chips():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")

    # Every provider the server picks is searched; there's nothing to switch
    assert 'id="metadata_provider"' not in template
    assert "pill" not in js and "/metadata/provider/" not in js


def test_description_box_fits_its_text_and_cannot_be_dragged():
    css = (REPO_ROOT / "cps/static/css/lily-library.css").read_text(encoding="utf-8")
    js = (REPO_ROOT / "cps/static/js/edit_books.js").read_text(encoding="utf-8")

    rule = css[css.index(".editbook-form .lily-description {"):]
    rule = rule[:rule.index("}")]
    assert "resize: none;" in rule
    assert "padding: 14px 16px;" in rule and "line-height: 1.68;" in rule
    # Refit after the web font loads and when the box's width changes
    assert "document.fonts.ready.then(fit);" in js
    assert "new ResizeObserver(" in js
