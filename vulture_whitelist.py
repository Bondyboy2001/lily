# Vulture whitelist.
#
# Names Vulture reports as unused but that are in fact referenced indirectly:
# Flask route/Jinja-filter callbacks, SQLAlchemy columns and event listeners,
# Flask-Login callbacks, the Wand/Pillow drawing API, werkzeug cache-control
# attributes and other dynamic references. Configuration lives in
# `[tool.vulture]` in pyproject.toml; run `vulture` (no arguments) from the repo
# root to check the source plus this whitelist.
#
# Add an entry here only for a name that is genuinely used but statically
# invisible. New *dead* code should be deleted, not whitelisted.

_ = object()  # noqa: F841 - dummy receiver used to reference attributes below

# Flask / Flask-Login config attributes set on the app (cps/__init__.py)
_.login_view
_.anonymous_user
_.session_protection

# Request-scoped `g` attributes read from templates
_.google_site_verification
_.allow_upload

# Per-user / per-task attributes assigned dynamically and read elsewhere
_.config_is_initial
_.illegal_characters
_.flask_httpauth_user
_._lily_cwa_db
_._lily_fetched_ids  # cached on g, read back with g.get() (jinjia.metadata_fetched)

# SQLAlchemy model columns, relationships and hybrid properties
_.atom_timestamp
_.custom_extra_fill
_.downloads
_.dismissed_at
logged_in

# SQLAlchemy event listeners + Flask-Login callbacks (invoked by the framework)
exc_info
initiator
oldvalue
flush_context
connection_record
_.get_id
load_user
_close_cwa_db

# Pagination properties consumed by Jinja templates (layout.html, feed.xml)
_.next_offset
_.previous_offset
_.has_prev
_.has_next
_.iter_pages

# Conditional-import fallbacks (the "advocate not installed" path)
advocate
MissingSchema

# werkzeug Response.cache_control attributes
_.no_cache
_.public
_.private
_.max_age
_.expires

# Wand/Pillow drawing attributes (read by the imaging library)
_.compression_quality
_.background_color
_.font_size
_.font
_.fill_color
_.text_antialias

# WSGI handler / logger internals
_.format_request
_.disabled

# Route parameters required by the URL rule signature
anyname

_.isolation_level

# Book page template reads entry.paper_doi (detail.html)
_.paper_doi

# CalibreTask.run(worker_thread) interface parameter; WorkerThread passes itself
worker_thread

# HTMLParser calls it for each tag (scholar._CitationTags)
_.handle_starttag
