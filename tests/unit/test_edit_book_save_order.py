from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_directory_update_happens_before_comments_and_tags_are_staged():
    source = (REPO_ROOT / "cps/editbooks.py").read_text(encoding="utf-8")
    function_body = source[source.index("def do_edit_book") : source.index("def merge_metadata")]

    directory_update = function_body.index("helper.update_dir_structure")
    comments_update = function_body.index("edit_book_comments")
    tags_update = function_body.index("edit_book_tags")

    assert directory_update < comments_update
    assert directory_update < tags_update


def test_a_providers_placeholder_cover_is_no_cover_change():
    from cps.editbooks import is_generic_cover
    # The static URL carries a cache-busting query; a provider with no cover sends it as is
    assert is_generic_cover("/static/generic_cover.svg?q=e06d636")
    assert is_generic_cover("http://lily.local/static/generic_cover.svg")
    assert not is_generic_cover("https://covers.example.org/generic_cover.svg.jpg")
    source = (REPO_ROOT / "cps/editbooks.py").read_text(encoding="utf-8")
    branch = source[source.index("elif is_generic_cover("):source.index("else:", source.index("elif is_generic_cover("))]
    assert "has_cover" not in branch
    js = (REPO_ROOT / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    assert "!/\\/generic_cover\\.svg(\\?|$)/.test(book.cover)" in js
