"""The one-time trim of existing sidebars to Lily's default set."""

import pytest


@pytest.fixture
def app_db(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from cps import ub, constants

    engine = create_engine("sqlite:///" + str(tmp_path / "app.db"))
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    everything = (constants.SIDEBAR_DUPLICATES << 1) - 1
    session.add(ub.User(name="reader", email="reader@example.org", role=0, sidebar_view=everything))
    session.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY, config_default_show INTEGER)"))
    session.execute(text("INSERT INTO settings VALUES (1, :v)"), {"v": everything})
    session.commit()
    yield session
    session.close()


@pytest.mark.unit
def test_existing_sidebars_and_default_are_trimmed_once(app_db, tmp_path):
    from sqlalchemy import text
    from cps import ub, constants

    expected = constants.DEFAULT_SIDEBAR | constants.DETAIL_RANDOM
    ub.migrate_default_sidebar(app_db)

    assert app_db.query(ub.User).one().sidebar_view == expected
    assert app_db.execute(text("SELECT config_default_show FROM settings")).scalar() == expected
    assert (tmp_path / ".lily_sidebar_trimmed").exists()


@pytest.mark.unit
def test_entries_switched_back_on_survive_later_starts(app_db):
    from cps import ub, constants

    ub.migrate_default_sidebar(app_db)
    user = app_db.query(ub.User).one()
    user.sidebar_view |= constants.SIDEBAR_HOT
    app_db.commit()

    ub.migrate_default_sidebar(app_db)

    assert app_db.query(ub.User).one().sidebar_view & constants.SIDEBAR_HOT


@pytest.mark.unit
def test_default_sidebar_is_the_core_views():
    from cps import constants

    for flag in (constants.SIDEBAR_RECENT, constants.SIDEBAR_AUTHOR, constants.SIDEBAR_SERIES,
                 constants.SIDEBAR_CATEGORY, constants.SIDEBAR_READ_AND_UNREAD):
        assert constants.DEFAULT_SIDEBAR & flag
    for flag in (constants.SIDEBAR_HOT, constants.SIDEBAR_DOWNLOAD, constants.SIDEBAR_RANDOM,
                 constants.SIDEBAR_FORMAT, constants.SIDEBAR_DUPLICATES):
        assert not constants.DEFAULT_SIDEBAR & flag


@pytest.mark.unit
def test_emptied_sidebars_get_the_defaults_back_once(app_db, tmp_path):
    from cps import ub, constants

    reader = app_db.query(ub.User).one()
    reader.sidebar_view = 0
    app_db.add(ub.User(name="Guest", email="guest@example.org", role=constants.ROLE_ANONYMOUS, sidebar_view=0))
    app_db.commit()

    ub.migrate_restore_emptied_sidebars(app_db)

    assert app_db.query(ub.User).filter_by(name="reader").one().sidebar_view == constants.DEFAULT_SIDEBAR
    assert app_db.query(ub.User).filter_by(name="Guest").one().sidebar_view == 0
    assert (tmp_path / ".lily_sidebar_restored").exists()

    # A user who later empties their sidebar on purpose keeps it empty.
    reader = app_db.query(ub.User).filter_by(name="reader").one()
    reader.sidebar_view = 0
    app_db.commit()
    ub.migrate_restore_emptied_sidebars(app_db)
    assert app_db.query(ub.User).filter_by(name="reader").one().sidebar_view == 0


@pytest.mark.unit
def test_saving_the_profile_leaves_the_sidebar_alone():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "cps/web_auth.py").read_text(encoding="utf-8")
    assert "current_user.sidebar_view =" not in source
    assert "key.startswith('show')" not in source
