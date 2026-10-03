/* This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
 *    Copyright (C) 2018  idalin<dalin.lin@gmail.com>
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
/* global _, i18nMsg, getPath */

// Fetch Metadata on the edit page and the book page. One search box takes a title and
// author, an ISBN, a DOI or an arXiv id; the server says which providers to ask, and each
// is asked separately so its results show as soon as they arrive, ranked with the rest.
// Apply fills #book_edit_frm with a result's ticked fields and saves. On the book page that
// form is hidden: the fields only a result may change start disabled, so an untouched one
// is not sent and the save leaves it as it is.
$(function () {
  var msg = i18nMsg;
  var $form = $("#book_edit_frm");
  var metaSelectionKey = "cwa.metaSelection";
  var metaSelectionCache = null;
  var FAILED = { error: true, timeout: true };

  var status = {};      // provider id -> "loading" | "ok" | "skipped" | "error" | "timeout"
  var results = [];     // {uid, provider, book, descText, $el}; uid is the index
  var query = "";
  var request = null;   // what every provider is sent for the current search
  var form = null;      // the edit form's values the cards compare against
  var searchSeq = 0;    // answers to an earlier search are dropped
  var inFlight = [];    // the current search's requests, aborted by a new search

  // Provider results are third-party data: the template escapes every value (<%- %>),
  // shows descriptions as plain text and only links to http(s) URLs.
  function safeUrl(url) {
    return /^https?:\/\//i.test(String(url || "")) ? String(url) : "";
  }

  // A provider often knows only the year or the month a book came out ("1965", "1965-08"):
  // the date field takes a whole date, so those begin on their first day
  function fullDate(date) {
    var parts = /^(\d{4})(?:-(\d{1,2}))?$/.exec($.trim(String(date || "")));
    if (!parts) { return date; }
    return parts[1] + "-" + ("0" + (parts[2] || "1")).slice(-2) + "-01";
  }

  function htmlToText(html) {
    // DOMParser documents run no scripts and load no images
    return new DOMParser().parseFromString(String(html || ""), "text/html").body.textContent || "";
  }

  // The form's fields by name: the book page's own ids (its h1 is #title) aren't the form's
  function field(name) {
    return $form.find("[name='" + name + "']");
  }

  // Sets a field, which the save then sends
  function set(name, value) {
    return field(name).prop("disabled", false).val(value);
  }

  // The editor's rating is a group of star radios (image.html rating_input); the book page's a hidden field
  function ratingInput() {
    return $form.find("input[name='rating']");
  }

  function ratingValue() {
    var $rating = ratingInput();
    return $rating.is(":radio") ? $rating.filter(":checked").val() : $rating.val();
  }

  var bookResultTemplate = _.template($("#template-book-result").html());

  function getMetaSelections() {
    if (metaSelectionCache !== null) {
      return metaSelectionCache;
    }
    try {
      var stored = localStorage.getItem(metaSelectionKey);
      metaSelectionCache = stored ? JSON.parse(stored) : {};
    } catch (e) {
      metaSelectionCache = {};
    }
    if (typeof metaSelectionCache !== "object" || metaSelectionCache === null) {
      metaSelectionCache = {};
    }
    // The cover starts unticked every time: it is chosen per book, never remembered
    delete metaSelectionCache.cover;
    return metaSelectionCache;
  }

  function saveMetaSelections(changes) {
    var selections = $.extend(getMetaSelections(), changes);
    try {
      localStorage.setItem(metaSelectionKey, JSON.stringify(selections));
    } catch (e) {
      // Ignore storage failures (quota/private mode)
    }
  }

  // A card's ticks as field -> checked
  function ticksOf($card) {
    var ticks = {};
    $card.find("[data-meta-value]").each(function () {
      ticks[this.dataset.metaValue] = this.checked;
    });
    return ticks;
  }

  function applyTicks($card, ticks) {
    $card.find("[data-meta-value]").each(function () {
      if (Object.prototype.hasOwnProperty.call(ticks, this.dataset.metaValue)) {
        this.checked = ticks[this.dataset.metaValue];
      }
    });
  }

  function getUniqueValues(attribute_name, book) {
    var presentArray = $.map(String(field(attribute_name).val() || "").split(","), $.trim).filter(Boolean);
    $.each(book[attribute_name] || [], function (i, el) {
      if ($.inArray(el, presentArray) === -1) presentArray.push(el);
    });
    return presentArray;
  }

  // The book's identifiers (ISBN, DOI, arXiv...) from the edit form, for exact lookups
  function currentIdentifiers() {
    var ids = {};
    $form.find("#identifier-table tr").each(function () {
      var type = $.trim($(this).find(".identifier-type").val() || "");
      var val = $.trim($(this).find(".identifier-val").val() || "");
      if (type && val) { ids[type.toLowerCase()] = val; }
    });
    return ids;
  }

  function currentAuthors() {
    return String(field("authors").val() || "").split("&").map($.trim)
      .filter(function (a) { return a && a.toLowerCase() !== "unknown"; });
  }

  function same(a, b) {
    return String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();
  }

  // The form's values, read once per render rather than once per card
  function readForm() {
    return {
      title: field("title").val(), authors: field("authors").val(), publisher: field("publisher").val(),
      pubdate: field("pubdate").val(), series: field("series").val(), seriesIndex: field("series_index").val(),
      rating: ratingValue(), description: htmlToText(field("comments").val()),
      tags: field("tags").val(), languages: field("languages").val(), ids: currentIdentifiers(),
    };
  }

  // The rows a result card shows: what the provider has, dimmed where the form already has it
  function buildFields(result) {
    var book = result.book;
    var fields = [];
    function add(key, label, text, current, extra) {
      if (text === undefined || text === null || text === "") return;
      fields.push($.extend({
        key: key, label: label, text: String(text),
        same: same(text, current),
      }, extra || {}));
    }
    var authors = (book.authors || []).join(" & ");
    add("title", msg.title, book.title, form.title, { link: book.url });
    add("authors", msg.author, authors, form.authors);
    add("publisher", msg.publisher, book.publisher, form.publisher);
    add("pubDate", msg.pubdate, book.publishedDate, form.pubdate);
    add("series", msg.series, book.series, form.series);
    if (book.series_index) {
      add("seriesIndex", msg.series_index, book.series_index, form.seriesIndex, {
        same: Number(book.series_index) === Number(form.seriesIndex),
      });
    }
    if (book.rating) {
      add("rating", msg.rating, book.rating + " / 5", form.rating ? form.rating + " / 5" : "");
    }
    add("description", msg.comments, result.descText, form.description, { cls: "meta-description" });
    if (book.tags && book.tags.length) {
      add("tags", msg.tags, book.tags.join(", "), form.tags);
    }
    if (book.languages && book.languages.length) {
      add("languages", msg.languages, book.languages.join(", "), form.languages);
    }
    $.each(book.identifiers || {}, function (key, value) {
      if (value === "" || value === null) return;
      add(key, key, value, form.ids[String(key).toLowerCase()], {
        // Editions found by that id are the exact matches; others offer the lookup
        editions: key === "hardcover-id" && !book.exact_match ? value : "",
      });
    });
    return fields;
  }

  function populateForm(result) {
    var book = result.book;
    var updateItems = ticksOf(result.$el);
    if (updateItems.description) {
      set("comments", book.description || "").trigger("lily:set-html");
    }
    if (updateItems.tags) {
      set("tags", getUniqueValues("tags", book).join(", ")).trigger("change");
    }
    if (updateItems.languages) {
      set("languages", getUniqueValues("languages", book).join(", "));
    }
    if (updateItems.authors) {
      set("authors", (book.authors || []).join(" & ")).trigger("change");
    }
    if (updateItems.title) {
      set("title", book.title);
    }
    if (updateItems.rating) {
      // In the editor, check the matching star, or none
      var $rating = ratingInput();
      if ($rating.is(":radio")) {
        var $star = $rating.filter("[value='" + Math.round(book.rating) + "']");
        ($star.length ? $star : $rating.filter("[value='']")).prop("checked", true);
      } else {
        $rating.val(Math.round(book.rating) || "");
      }
    }
    // A provider with no cover sends Lily's placeholder; that leaves the book's own cover alone.
    if (updateItems.cover && book.cover && !/\/generic_cover\.svg(\?|$)/.test(book.cover) && field("cover_url").length) {
      $(".cover img").attr("src", book.cover);
      set("cover_url", book.cover);
    }
    if (updateItems.pubDate) {
      set("pubdate", fullDate(book.publishedDate)).trigger("change");
    }
    if (updateItems.publisher) {
      set("publisher", book.publisher);
    }
    if (updateItems.series && book.series) {
      set("series", book.series);
    }
    if (updateItems.seriesIndex && book.series_index) {
      set("series_index", book.series_index);
    }
    $.each(book.identifiers || {}, function (key, value) {
      if (updateItems[key] && value !== "" && value !== null) {
        setIdentifier(key, String(value));
      }
    });
    // A result from arXiv carries the paper's arXiv id: add an arXiv chip to the Shelves
    // editor, which the save turns into the shelf
    var $shelves = field("shelves");
    if (book.identifiers && book.identifiers.arxiv && $shelves.length) {
      var names;
      try { names = JSON.parse($shelves.val() || "[]"); } catch (e) { names = []; }
      if (!names.some(function (n) { return n.toLowerCase() === "arxiv"; })) {
        names.push("arXiv");
        set("shelves", JSON.stringify(names)).trigger("change");
        set("shelves_present", "1");
      }
    }
    // The save notes the book as matched by this provider (editbooks.py)
    set("metadata_source", (book.source && book.source.description) || "Fetch Metadata");
    $form.trigger("lily:reveal-filled");
  }

  // The editor saves through its Save button; the book page's hidden form has none
  function save() {
    $("#metaModal").modal("hide");
    var $submit = $form.find("#submit");
    if ($submit.length) {
      $submit.trigger("click");
    } else {
      $form[0].submit();
    }
  }

  function setIdentifier(type, value) {
    var normalized = type.trim().toLowerCase();
    var $row = $form.find("#identifier-table tbody tr").filter(function () {
      return ($(this).find("input.identifier-type").val() || "").trim().toLowerCase() === normalized;
    }).first();
    if ($row.length) {
      $row.find("input.identifier-val").val(value);
      return;
    }
    // Built with .val() rather than markup: identifiers come from the providers
    var randId = Math.floor(Math.random() * 1000000).toString();
    function cell(cls, name, placeholder, val) {
      return $("<td>").append($("<input>", {
        type: "text", "class": "form-control " + cls, name: name + randId,
        required: "required", placeholder: placeholder,
      }).val(val));
    }
    $("<tr>")
      .append(cell("identifier-type", "identifier-type-", msg.identifier_type, type))
      .append(cell("identifier-val", "identifier-val-", msg.identifier_value, value))
      .append($("<td>").append($("<button>", { type: "button", "class": "btn btn-danger identifier-remove" }).text(msg.remove)))
      .appendTo($form.find("#identifier-table tbody"));
  }

  // ---- Results ----

  function renderCard(result) {
    return $(bookResultTemplate({
      book: result.book, uid: result.uid, fields: buildFields(result), safeUrl: safeUrl,
    }));
  }

  function showMessage(text, isError) {
    $("#meta-info").empty().append($("<p>", { "class": isError ? "text-danger" : "text-muted" }).text(text));
  }

  // Exact identifier matches first, then the best match to the book; cards are
  // moved, not redrawn, so ticks survive new results arriving
  function renderResults() {
    var shown = results.slice();
    shown.sort(function (a, b) {
      return (b.book.exact_match - a.book.exact_match) ||
        ((b.book.score || 0) - (a.book.score || 0)) || (a.uid - b.uid);
    });
    var loading = Object.keys(status).some(function (id) { return status[id] === "loading"; });
    if (!shown.length) {
      if (loading) {
        showMessage(msg.loading);
      } else if (query) {
        var failed = Object.keys(status).some(function (id) { return FAILED[status[id]]; });
        showMessage(failed ? msg.search_error : msg.no_result, failed);
      }
      return;
    }
    var $list = $("#book-list");
    if (!$list.length) {
      $list = $('<ul id="book-list" class="media-list"></ul>');
      $("#meta-info").empty().append($list);
    }
    $list.children().detach();
    shown.forEach(function (r) { $list.append(r.$el); });
  }

  function searchProvider(providerId, seq) {
    status[providerId] = "loading";
    inFlight.push($.ajax({
      url: getPath() + "/metadata/search",
      type: "POST",
      dataType: "json",
      data: $.extend({ provider: providerId }, request),
    }).done(function (data) {
      if (seq !== searchSeq) return;
      status[providerId] = data.status || "ok";
      (data.results || []).forEach(function (book) {
        var result = { uid: results.length, provider: providerId, book: book, descText: htmlToText(book.description) };
        result.$el = renderCard(result);
        applyTicks(result.$el, getMetaSelections());
        results.push(result);
      });
    }).fail(function () {
      if (seq !== searchSeq) return;
      status[providerId] = "error";
    }).always(function () {
      if (seq === searchSeq) renderResults();
    }));
  }

  // Asks every provider the server picks for the query
  function searchAll() {
    var seq = searchSeq;
    inFlight.push($.getJSON(getPath() + "/metadata/provider", { query: query }).done(function (ids) {
      if (seq !== searchSeq) return;
      ids.forEach(function (id) { searchProvider(id, seq); });
      renderResults();
    }));
  }

  function runSearch(text) {
    query = $.trim(text || "");
    searchSeq += 1;
    inFlight.forEach(function (xhr) { xhr.abort(); });
    inFlight = [];
    results = [];
    status = {};
    form = readForm();
    request = {
      query: query,
      book_id: $("#metaModal").data("book-id"),
      identifiers: JSON.stringify(form.ids),
      title: form.title,
      authors: form.authors,
    };
    if (query) {
      showMessage(msg.loading);
      searchAll();
    } else {
      $("#meta-info").empty();
    }
  }

  // ---- Events ----

  $(document).on("change", '#meta-info input[type="checkbox"][data-meta-value]', function () {
    if (this.dataset.metaValue === "cover") { return; }
    var change = {};
    change[this.dataset.metaValue] = this.checked;
    saveMetaSelections(change);
  });

  function resultFor(el) {
    return results[Number($(el).closest("li.media").data("uid"))];
  }

  $("#meta-info").on("click", ".meta-apply", function () {
    var r = resultFor(this);
    if (r) {
      populateForm(r);
      save();
    }
  });

  $("#meta-info").on("click", ".meta-editions", function (e) {
    e.preventDefault();
    var text = "hardcover-id:" + $(this).data("hardcover-id");
    $("#keyword").val(text);
    runSearch(text);
  });

  $("#meta-search").on("submit", function (e) {
    e.preventDefault();
    runSearch($("#keyword").val());
  });

  // The editor's Fetch metadata button and the book page's
  $("#get_meta, #fetch_book_meta").click(function () {
    // Title and first author: a title alone finds every book of that name
    var text = $.trim([field("title").val(), currentAuthors()[0] || ""].join(" "));
    $("#keyword").val(text);
    runSearch(text);
  });

  // The book page's "Fetch metadata" link arrives with ?fetch=1: open the lookup straight away
  if (new URLSearchParams(window.location.search).has("fetch")) {
    var clean = new URL(window.location.href);
    clean.searchParams.delete("fetch");
    window.history.replaceState(window.history.state, "", clean.pathname + clean.search + clean.hash);
    $("#get_meta").trigger("click");
  }

  // The dialog opens beside the cover, not over it, so results can be compared with
  // the book's own cover. Only when at least 440px is left for it (a phone); otherwise centred.
  // Rects and innerWidth are in window pixels, margins in CSS pixels under the page
  // zoom (lily.css), so divide by it.
  function placeBesideCover() {
    var dialog = $("#metaModal .modal-dialog")[0];
    // The book page's whole plate, so the dialog clears its frame too
    var cover = $(".editbook-cover-section .cover, .book-detail-cover")[0];
    var zoom = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--page-zoom")) || 1;
    var left = cover ? Math.round(cover.getBoundingClientRect().right / zoom + 24) : 0;
    var room = Math.floor(window.innerWidth / zoom - left - 24);
    var fits = left > 0 && room >= 440;
    dialog.classList.toggle("meta-beside-cover", fits);
    dialog.style.setProperty("--meta-left", fits ? left + "px" : "");
    dialog.style.setProperty("--meta-room", fits ? room + "px" : "");
  }

  $("#metaModal").on("show.bs.modal", function (e) {
    placeBesideCover();
    $(e.relatedTarget).one("focus", function () {
      $(this).blur();
    });
  });
  $(window).on("resize", function () {
    if ($("#metaModal").hasClass("in")) { placeBesideCover(); }
  });
});
