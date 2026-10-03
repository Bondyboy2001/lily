from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_fetch_metadata_and_the_editor_have_no_description():
    get_meta = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    edit_js = (REPO_ROOT / "cps/static/js/edit_books.js").read_text(encoding="utf-8")
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    css = (REPO_ROOT / "cps/static/css/lily-library.css").read_text(encoding="utf-8")

    # Lily keeps no descriptions: a result never shows or applies one, and the editor has no box
    assert "book.description" not in get_meta and 'set("comments"' not in get_meta
    assert "book.description" not in template
    assert "lily:set-html" not in edit_js and "lily-description" not in css


def test_metadata_result_button_is_apply_not_save():
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")

    assert 'class="btn btn-default meta-apply"' in template
    assert '>{{_("Apply")}}</button>' in template
    # Apply is the only action: no Fill form or Tick all / none
    assert "meta-fill" not in template and "meta-toggle-all" not in template


def test_metadata_source_links_to_the_result_itself():
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")

    # An arXiv result links to its abstract page, not arxiv.org
    assert 'href="<%- safeUrl(book.url) || safeUrl(book.source.link) %>"' in template


def test_match_score_is_a_big_number_in_the_cards_top_right():
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    css = (REPO_ROOT / "cps/static/css/lily-library.css").read_text(encoding="utf-8")

    # Right after the cover column closes, as the card's own corner badge
    assert ('    </div>\n    <% if (!book.exact_match && book.score) { %>\n'
            '    <div class="meta-score"') in template
    assert "<%- Math.round(book.score * 100) %>%</div>" in template
    assert "#meta-info #book-list .media .meta-score {\n    position: absolute;" in css


def test_fetch_metadata_has_no_provider_chips():
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")

    # Every provider the server picks is searched; there's nothing to switch
    assert 'id="metadata_provider"' not in template
    assert "pill" not in js and "/metadata/provider/" not in js


def test_saving_always_opens_the_book_page():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")

    assert '<input type="hidden" name="detail_view" value="1">' in template
    assert 'name="detail_view" type="checkbox"' not in template


def test_fetch_metadata_sends_the_book_id_so_its_pdf_can_be_read():
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")

    assert 'id="metaModal" data-book-id="{{ book_id }}"' in template
    assert 'book_id: $("#metaModal").data("book-id"),' in js


def test_book_edit_has_no_rich_text_editor():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")
    js = (REPO_ROOT / "cps/static/js/edit_books.js").read_text(encoding="utf-8")

    # Comment columns are plain textareas; the HTML is sanitised server-side.
    assert "tinymce" not in template.lower() and "tiny_editor" not in template
    assert "tinymce" not in js.lower()
    assert not (REPO_ROOT / "cps/static/js/libs/tinymce").exists()


def test_fetch_metadata_closes_from_its_header_cross_only():
    html = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    assert 'class="close" data-dismiss="modal"' in html
    assert "modal-footer" not in html and "meta_close" not in html


def test_fetch_metadata_ticks_every_field_but_the_cover_and_remembers_nothing():
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    # Ticks come from the result and the book, per card, never from earlier books
    assert "localStorage" not in js and "metaSelection" not in js
    assert '<% if (f.tick) { %> checked<% } %>' in template
    assert "field.tick = true;" in js and "function ticked(" not in js
    assert '<input type="checkbox" data-meta-value="cover" aria-label=' in template
    # A result shows only the provider's values, no "Now: …" line of the book's own
    assert "meta-current" not in template and "Now:" not in template


def test_fetch_metadata_names_providers_that_did_not_answer():
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    template = (REPO_ROOT / "cps/templates/meta_fetch.html").read_text(encoding="utf-8")
    assert '<p id="meta-status" class="meta-status" role="status" hidden></p>' in template
    assert "function renderStatus()" in js and "needsKey[id]" in js
    assert "Search error!" not in template


def test_fetch_metadata_cards_stack_on_phones():
    css = (REPO_ROOT / "cps/static/css/lily-library.css").read_text(encoding="utf-8")
    phone = css[css.index("/* Phones: the card stacks"):]
    phone = phone[:phone.index("\n}\n")]
    assert "#meta-info #book-list .media { flex-direction: column; }" in phone
    assert ".meta-score + .media-body { padding: 10px 14px 14px; }" in phone


def test_a_ticked_field_replaces_the_books_value():
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    # A result never brings tags, a publisher, languages or a rating
    assert 'set("tags"' not in js and "msg.tags" not in js
    assert "getUniqueValues" not in js
    # A title's "(2nd Edition)" replaces the Edition field, filled or not
    assert "if (split.edition && $edition.length) {" in js
    # A case-only difference counts as a change, so it isn't dimmed and applies
    same = js[js.index("function same(a, b)"):]
    same = same[:same.index("}")]
    assert "toLowerCase" not in same


def test_fetch_metadata_breaks_near_ties_by_provider_order_not_arrival():
    """Hardcover comes before Open Library in the server's order (search_metadata.cl); a
    result as good from each lists Hardcover's first, whichever answered first."""
    from cps.search_metadata import cl
    ids = [c.__id__ for c in cl]
    assert ids.index("hardcover") < ids.index("openlibrary")
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    assert "rank[provider.id] = i;" in js
    assert "(scoreBand(b) - scoreBand(a)) ||\n        ((rank[a.provider] || 0) - (rank[b.provider] || 0))" in js
