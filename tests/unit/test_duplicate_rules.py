"""Pure duplicate rules: hashing, title normalisation and which book to keep."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def rules():
    from cps import duplicate_rules
    return duplicate_rules


def _book(ts=None, formats=(), tags=(), size=0, comment=None):
    data = [SimpleNamespace(format=f, uncompressed_size=size) for f in formats]
    comments = [SimpleNamespace(text=comment)] if comment else []
    return SimpleNamespace(timestamp=ts, data=data, tags=list(tags), series=None, ratings=[],
                           comments=comments, publishers=[], pubdate=None, identifiers=[])


def test_group_hash_ignores_case_and_padding_and_has_defaults(rules):
    assert rules.generate_group_hash("  Dune ", "FRANK Herbert") == rules.generate_group_hash("dune", "frank herbert")
    assert rules.generate_group_hash(None, None) == rules.generate_group_hash("untitled", "unknown")
    assert len(rules.generate_group_hash("a", "b")) == 32
    assert rules.generate_group_hash("a", "b") != rules.generate_group_hash("a", "c")


def test_title_normalisation_strips_a_leading_author_prefix(rules):
    assert rules.normalize_title_for_duplicates("Homer, The Iliad", "Homer") == "the iliad"
    assert rules.normalize_title_for_duplicates("The Iliad", "Homer") == "the iliad"
    assert rules.normalize_title_for_duplicates(None) == "untitled"


def test_strategy_validation(rules):
    assert rules.validate_resolution_strategy("newest") and rules.validate_resolution_strategy("merge")
    assert not rules.validate_resolution_strategy("random") and not rules.validate_resolution_strategy(None)


def test_newest_oldest_and_empty(rules):
    old = _book(datetime(2020, 1, 1))
    new = _book(datetime(2024, 1, 1, tzinfo=timezone.utc))  # mixing naive and aware must not raise
    assert rules.select_book_to_keep([old, new], "newest") is new
    assert rules.select_book_to_keep([old, new], "oldest") is old
    assert rules.select_book_to_keep([old, new], "merge") is new
    assert rules.select_book_to_keep([], "newest") is None
    assert rules.select_book_to_keep([old, new], "no-such-strategy") is new


def test_largest_file_size_and_most_metadata(rules):
    small = _book(datetime(2024, 1, 1), formats=["EPUB"], size=10)
    big = _book(datetime(2020, 1, 1), formats=["EPUB"], size=999)
    assert rules.select_book_to_keep([small, big], "largest_file_size") is big
    plain = _book(datetime(2024, 1, 1))
    rich = _book(datetime(2020, 1, 1), tags=["a", "b"], comment="x" * 80)
    assert rules.select_book_to_keep([plain, rich], "most_metadata") is rich


def test_highest_quality_format_uses_configured_priority_with_default_fallback(rules, monkeypatch):
    class _Boom:
        def __init__(self):
            raise RuntimeError("no settings db")

    monkeypatch.setattr(rules, "CWA_DB", _Boom)  # falls back to the built-in priority table
    pdf = _book(datetime(2024, 1, 1), formats=["PDF"])
    epub = _book(datetime(2020, 1, 1), formats=["EPUB"])
    assert rules.select_book_to_keep([pdf, epub], "highest_quality_format") is epub

    class _Settings:
        def __init__(self):
            self.cwa_settings = {"duplicate_format_priority": '{"PDF": 500, "EPUB": 1}'}

    monkeypatch.setattr(rules, "CWA_DB", _Settings)
    assert rules.select_book_to_keep([pdf, epub], "highest_quality_format") is pdf


def test_titles_fold_case_accents_and_punctuation(rules):
    fold = rules.normalize_title_for_duplicates
    assert fold("Low-Dimensional  Topology") == fold("low dimensional topology") == "low dimensional topology"
    assert fold("A2.2: Complex Analysis") == fold("A2.2  Complex Analysis")
    assert fold("Erdős on Graphs") == "erdos on graphs"


def test_authors_match_by_surname_and_first_initial(rules):
    author = rules.normalize_author_for_duplicates
    assert author("Ricardo Baptista") == author("R. Baptista") == author("Baptista| Ricardo") == "baptista r"
    assert author("William H. Meeks III") == author("Meeks III| W.") == "meeks w"
    assert author("Johannson") == "johannson"
    assert author("Unknown") == author("") == author(None) == ""
    assert author("Ricardo Baptista") != author("Rebecca Morrison")


def test_a_group_is_keyed_by_its_books_in_any_order(rules):
    assert rules.group_hash_for_books([3, 1, 2]) == rules.group_hash_for_books((2, 3, 1))
    assert rules.group_hash_for_books([1, 2]) != rules.group_hash_for_books([1, 2, 3])


class _Dismissals:
    """ub stub: DismissedDuplicateGroup rows as (user_id, group_hash)."""

    def __init__(self, rows, fail=False):
        self.rows, self.fail = rows, fail
        outer = self

        class _Query:
            def __init__(self, user_id=None):
                self.user_id = user_id

            def filter(self, expression):
                return _Query(expression)

            def all(self):
                if outer.fail:
                    raise RuntimeError("db locked")
                return [(group_hash,) for user_id, group_hash in outer.rows
                        if self.user_id is None or user_id == self.user_id]

        self.session = SimpleNamespace(query=lambda column: _Query())


@pytest.fixture
def detection(monkeypatch):
    from cps import duplicate_detection

    def install(rows, fail=False):
        ub = _Dismissals(rows, fail)
        # `DismissedDuplicateGroup.user_id == user_id` evaluates to the user id itself
        ub.DismissedDuplicateGroup = SimpleNamespace(group_hash="group_hash", user_id=_UserIdColumn())
        monkeypatch.setattr(duplicate_detection, "ub", ub)
        monkeypatch.setattr(duplicate_detection, "current_user", None)
        return duplicate_detection
    return install


class _UserIdColumn:
    def __eq__(self, user_id):
        return user_id


GROUPS = [{"group_hash": "a"}, {"group_hash": "b", "legacy_group_hash": "old-b"}, {"group_hash": "c"}]


def test_a_user_sees_every_group_but_the_ones_they_dismissed(detection):
    module = detection([(1, "a"), (2, "c"), (1, "old-b")])
    assert module.filter_dismissed_groups(GROUPS, user_id=2) == [GROUPS[0], GROUPS[1]]
    # A group dismissed before groups were keyed by their books stays dismissed
    assert module.filter_dismissed_groups(GROUPS, user_id=1) == [GROUPS[2]]


def test_background_scans_skip_groups_any_user_dismissed(detection):
    module = detection([(1, "a"), (2, "c")])
    assert module.filter_dismissed_groups(GROUPS) == [GROUPS[1]]


def test_background_scans_resolve_nothing_when_dismissals_cannot_be_read(detection):
    module = detection([], fail=True)
    assert module.filter_dismissed_groups(GROUPS) == []
    assert module.filter_dismissed_groups(GROUPS, user_id=1) == GROUPS
