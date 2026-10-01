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

// Fetch Metadata on the edit page. One search box takes a title and author, an ISBN,
// a DOI or an arXiv id; the server says which providers to ask, and each is asked
// separately so its results show as soon as they arrive, ranked with the rest. A result's ticked fields can fill the form
// (several results can be combined) or fill it and save straight away.
$(function () {
  var msg = i18nMsg;
  var metaSelectionKey = "cwa.metaSelection";
  var metaSelectionCache = null;
  var metaAlertTimer = null;
  var SCHOLAR = "googlescholar";
  var STATUS = {
    loading: { note: msg.searching, cls: "is-loading" },
    error: { note: msg.failed, cls: "is-failed" },
    timeout: { note: msg.timed_out, cls: "is-failed" },
  };

  var providers = [];   // {id, name, active, search} from the server, for the query
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

  function htmlToText(html) {
    // DOMParser documents run no scripts and load no images
    return new DOMParser().parseFromString(String(html || ""), "text/html").body.textContent || "";
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
    var presentArray = $.map(String($("#" + attribute_name).val() || "").split(","), $.trim).filter(Boolean);
    $.each(book[attribute_name] || [], function (i, el) {
      if ($.inArray(el, presentArray) === -1) presentArray.push(el);
    });
    return presentArray;
  }

  // The book's identifiers (ISBN, DOI, arXiv...) from the edit form, for exact lookups
  function currentIdentifiers() {
    var ids = {};
    $("#identifier-table tr").each(function () {
      var type = $.trim($(this).find(".identifier-type").val() || "");
      var val = $.trim($(this).find(".identifier-val").val() || "");
      if (type && val) { ids[type.toLowerCase()] = val; }
    });
    return ids;
  }

  function currentAuthors() {
    return String($("#authors").val() || "").split("&").map($.trim)
      .filter(function (a) { return a && a.toLowerCase() !== "unknown"; });
  }

  function same(a, b) {
    return String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();
  }

  function shorten(text) {
    text = String(text || "");
    return text.length > 160 ? text.slice(0, 160) + "…" : text;
  }

  // The form's values, read once per render rather than once per card
  function readForm() {
    return {
      title: $("#title").val(), authors: $("#authors").val(), publisher: $("#publisher").val(),
      pubdate: $("#pubdate").val(), series: $("#series").val(), seriesIndex: $("#series_index").val(),
      rating: $("#rating").val(), description: htmlToText($("#comments").val()),
      tags: $("#tags").val(), languages: $("#languages").val(), ids: currentIdentifiers(),
    };
  }

  // The rows a result card shows: what the provider has, and what the form has now
  function buildFields(result) {
    var book = result.book;
    var fields = [];
    function add(key, label, text, current, extra) {
      if (text === undefined || text === null || text === "") return;
      fields.push($.extend({
        key: key, label: label, text: String(text),
        current: current ? shorten(current) : "",
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
      $("#comments").val(book.description || "").trigger("lily:set-html");
    }
    if (updateItems.tags) {
      $("#tags").val(getUniqueValues("tags", book).join(", ")).trigger("change");
    }
    if (updateItems.languages) {
      $("#languages").val(getUniqueValues("languages", book).join(", "));
    }
    if (updateItems.authors) {
      $("#authors").val((book.authors || []).join(" & ")).trigger("change");
    }
    if (updateItems.title) {
      $("#title").val(book.title);
    }
    if (updateItems.rating) {
      var roundedRating = Math.round(book.rating);
      var ratingWidget = $("#rating").data("rating");
      if (ratingWidget && typeof ratingWidget.setValue === "function") {
        ratingWidget.setValue(roundedRating);
      }
      $("#rating").val(roundedRating);
    }
    if (updateItems.cover && book.cover && $("#cover_url").length) {
      $(".cover img").attr("src", book.cover);
      $("#cover_url").val(book.cover);
    }
    if (updateItems.pubDate) {
      $("#pubdate").val(book.publishedDate).trigger("change");
    }
    if (updateItems.publisher) {
      $("#publisher").val(book.publisher);
    }
    if (updateItems.series && book.series) {
      $("#series").val(book.series);
    }
    if (updateItems.seriesIndex && book.series_index) {
      $("#series_index").val(book.series_index);
    }
    $.each(book.identifiers || {}, function (key, value) {
      if (updateItems[key] && value !== "" && value !== null) {
        setIdentifier(key, String(value));
      }
    });
    // Scholar results (arXiv/Crossref) are papers: add a Papers chip to the Shelves
    // editor, which the save turns into the shelf
    var $shelves = $("#shelves");
    if (book.source && book.source.id === SCHOLAR && $shelves.length) {
      var names;
      try { names = JSON.parse($shelves.val() || "[]"); } catch (e) { names = []; }
      if (!names.some(function (n) { return n.toLowerCase() === "papers"; })) {
        names.push("Papers");
        $shelves.val(JSON.stringify(names)).trigger("change");
      }
    }
    $("#meta_save").prop("hidden", false);
    showFilledAlert();
    // The cards' "Now" notes describe the form, which just changed
    form = readForm();
    results.forEach(refreshCard);
  }

  function showFilledAlert() {
    var $alert = $("#meta-import-alert");
    if (metaAlertTimer) {
      clearTimeout(metaAlertTimer);
    }
    $alert.show().addClass("is-visible");
    metaAlertTimer = setTimeout(function () {
      $alert.removeClass("is-visible");
      setTimeout(function () { $alert.hide(); }, 250);
    }, 2500);
  }

  function save() {
    $('#book_edit_frm input[name="detail_view"]').prop("checked", true);
    $("#metaModal").modal("hide");
    $("#submit").trigger("click");
  }

  function setIdentifier(type, value) {
    var normalized = type.trim().toLowerCase();
    var $row = $("#identifier-table tbody tr").filter(function () {
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
      .appendTo($("#identifier-table tbody"));
  }

  // ---- Results ----

  function renderCard(result) {
    return $(bookResultTemplate({
      book: result.book, uid: result.uid, fields: buildFields(result), safeUrl: safeUrl,
    }));
  }

  // Redraws a card from the form's current values, keeping its ticks
  function refreshCard(result) {
    var $el = renderCard(result);
    applyTicks($el, ticksOf(result.$el));
    result.$el.replaceWith($el);
    result.$el = $el;
  }

  // Results show for providers being searched; switching one off hides its results
  function isShown(providerId) {
    var p = providers.find(function (x) { return x.id === providerId; });
    return p ? p.search : true;
  }

  function showMessage(text, isError) {
    $("#meta-info").empty().append($("<p>", { "class": isError ? "text-danger" : "text-muted" }).text(text));
  }

  // Exact identifier matches first, then the best match to the book; cards are
  // moved, not redrawn, so ticks survive new results arriving
  function renderResults() {
    var shown = results.filter(function (r) { return isShown(r.provider); });
    shown.sort(function (a, b) {
      return (b.book.exact_match - a.book.exact_match) ||
        ((b.book.score || 0) - (a.book.score || 0)) || (a.uid - b.uid);
    });
    var loading = Object.keys(status).some(function (id) {
      return status[id] === "loading" && isShown(id);
    });
    if (!shown.length) {
      if (loading) {
        showMessage(msg.loading);
      } else if (query) {
        var failed = Object.keys(status).some(function (id) { return STATUS[status[id]]; });
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

  function renderProviders() {
    var $box = $("#metadata_provider").empty();
    providers.forEach(function (p) {
      var inputId = "show-" + p.id;
      var count = results.filter(function (r) { return r.provider === p.id; }).length;
      var state = STATUS[status[p.id]] || (status[p.id] === "ok" ? { note: String(count), cls: "" } : null);
      var $label = $("<label>", { "for": inputId }).text(p.name + " ");
      if (state) {
        $label.append($("<span>", { "class": "meta-provider-status " + state.cls }).text(state.note));
      }
      $label.append(' <span class="glyphicon glyphicon-ok" aria-hidden="true"></span>');
      $box.append(
        $("<input>", { type: "checkbox", id: inputId, "class": "pill", "data-control": p.id })
          .prop("checked", p.active),
        $label
      );
    });
  }

  function render() {
    renderProviders();
    renderResults();
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
      if (seq === searchSeq) render();
    }));
  }

  // Asks every provider the server picks for the query that hasn't been asked yet
  function searchPending() {
    var seq = searchSeq;
    inFlight.push($.getJSON(getPath() + "/metadata/provider", { query: query }).done(function (data) {
      if (seq !== searchSeq) return;
      providers = data;
      providers.forEach(function (p) {
        if (p.search && !status[p.id]) searchProvider(p.id, seq);
      });
      render();
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
      identifiers: JSON.stringify(form.ids),
      title: form.title,
      authors: form.authors,
    };
    if (query) {
      showMessage(msg.loading);
      searchPending();
    } else {
      $("#meta-info").empty();
      renderProviders();
    }
  }

  // ---- Events ----

  $(document).on("change", "#metadata_provider .pill", function () {
    var id = $(this).data("control");
    var p = providers.find(function (x) { return x.id === id; });
    if (!p) return;
    p.active = $(this).prop("checked");
    // The server then says what to search: switching off hides that provider's
    // results, switching on searches it
    $.ajax({
      method: "post",
      contentType: "application/json; charset=utf-8",
      url: getPath() + "/metadata/provider/" + id,
      data: JSON.stringify({ value: p.active }),
    }).done(function () {
      if (query) searchPending();
    });
  });

  $(document).on("change", '#meta-info input[type="checkbox"][data-meta-value]', function () {
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

  $("#meta-info").on("click", ".meta-fill", function () {
    var r = resultFor(this);
    if (r) populateForm(r);
  });

  $("#meta-info").on("click", ".meta-toggle-all", function () {
    var $card = $(this).closest("li.media");
    var $boxes = $card.find("[data-meta-value]");
    $boxes.prop("checked", $boxes.filter(":checked").length < $boxes.length);
    saveMetaSelections(ticksOf($card));
  });

  $("#meta-info").on("click", ".meta-editions", function (e) {
    e.preventDefault();
    var text = "hardcover-id:" + $(this).data("hardcover-id");
    $("#keyword").val(text);
    runSearch(text);
  });

  $("#meta_save").on("click", save);

  $("#meta-search").on("submit", function (e) {
    e.preventDefault();
    runSearch($("#keyword").val());
  });

  $("#get_meta").click(function () {
    // Title and first author: a title alone finds every book of that name
    var text = $.trim([$("#title").val(), currentAuthors()[0] || ""].join(" "));
    $("#keyword").val(text);
    runSearch(text);
  });

  $("#metaModal").on("show.bs.modal", function (e) {
    $(e.relatedTarget).one("focus", function () {
      $(this).blur();
    });
  });
});
