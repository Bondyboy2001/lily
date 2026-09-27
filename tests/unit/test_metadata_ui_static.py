from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_metadata_description_apply_syncs_the_description_editor():
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")

    assert 'var description = book.description || "";' in js
    # The description box is a plain textarea now, not TinyMCE, so Apply sets its
    # value and fires lily:set-html for edit_books.js to pick up.
    assert '$("#comments").val(description).trigger("lily:set-html");' in js


def test_description_editor_listens_for_lily_set_html():
    js = (REPO_ROOT / "cps/static/js/edit_books.js").read_text(encoding="utf-8")

    assert '$box.on("lily:set-html", function () {' in js


def test_metadata_result_button_is_apply_not_save():
    template = (REPO_ROOT / "cps/templates/book_edit.html").read_text(encoding="utf-8")

    assert '<button class="btn btn-default">{{_("Apply")}}</button>' in template
