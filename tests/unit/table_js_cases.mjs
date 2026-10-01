import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../cps/static/js/table.js", import.meta.url), "utf8");

function makeEl(name) {
    const el = {
        __name: name,
        __calls: [],
        __store: {},
        __once: {},
        __delegated: {},
        __click: null,
        __children: [],
        __text: "",
        length: 1,
        _record(method, args) { el.__calls.push([method, args]); return el; },
        on(ev, sel, fn) {
            const handler = typeof sel === "function" ? sel : fn;
            const key = typeof sel === "function" ? ev + ":*" : ev + ":" + sel;
            (el.__delegated[key] = el.__delegated[key] || []).push(handler);
            return el;
        },
        one(ev, fn) { (el.__once[ev] = el.__once[ev] || []).push(fn); return el; },
        click(fn) { el.__click = fn; return el; },
        trigger(ev) {
            if (el.__once[ev]) { el.__once[ev].forEach((f) => f.call(el)); el.__once[ev] = []; }
            return el;
        },
        fire(sel, ev) {
            (el.__delegated[(ev || "click") + ":" + sel] || []).forEach((f) => f.call(el, {}));
            return el;
        },
        bootstrapTable(cmd, arg) { el.__calls.push(["bootstrapTable", [cmd, arg]]); return el; },
        prop(n, v) { if (v === undefined) return el.__store["prop:" + n]; el.__store["prop:" + n] = v; return el; },
        attr(n, v) { if (v === undefined) return el.__store["attr:" + n]; el.__store["attr:" + n] = v; return el; },
        data(n, v) { if (v === undefined) return el.__store["data:" + n]; el.__store["data:" + n] = v; return el; },
        val(v) { if (v === undefined) return el.__store.val || ""; el.__store.val = v; return el; },
        text(v) { if (v === undefined) return el.__text; el.__text = String(v); return el; },
        html(v) { if (v === undefined) return el.__text; el.__text = String(v); return el; },
        is() { return false; },
        hasClass() { return false; },
        index() { return 0; },
        append(c) { el.__children.push(c); return el; },
        appendTo(target) { if (target && target.__children) target.__children.push(el); return el; },
        first() { return el; },
        closest() { return el; },
        find() { return el; },
        each() { return el; },
        map() { return el; },
        filter() { return el; },
        get() { return el; },
        eq() { return el; },
        toArray() { return []; },
        empty() { el.__children = []; return el; },
        textContent: "",
    };
    return new Proxy(el, {
        get(t, prop) {
            if (prop in t) return t[prop];
            return () => el;
        },
        set(t, prop, v) { t[prop] = v; return true; },
    });
}

export function loadTable() {
    const els = {};
    const ajaxCalls = [];
    const flashes = [];
    const elFor = (sel) => (els[sel] = els[sel] || makeEl(sel));
    const doc = makeEl("document");
    const context = {
        console, JSON, String, Number, Boolean, Array, Object, Math, Date, RegExp,
        parseInt, parseFloat, isNaN, setTimeout, clearTimeout, setInterval, clearInterval,
    };
    context.window = context;
    context.globalThis = context;
    context.document = {
        addEventListener() {},
        createElement: (tag) => makeEl("<" + tag + ">"),
        getElementById: (id) => elFor("#" + id),
        querySelector: (s) => elFor(s),
        body: makeEl("body"),
        readyState: "complete",
        location: { href: "", pathname: "/" },
    };
    context.location = context.document.location;
    context._ = {
        union: (a, b) => [...new Set([...a, ...b])],
        difference: (a, b) => a.filter((x) => !b.includes(x)),
    };
    context.lilyFlash = (msg, tone) => { flashes.push([msg, tone]); };
    context.getPath = () => "";
    context.confirmDialog = () => true;
    context.$ = function (arg) {
        if (typeof arg === "function") { arg(); return elFor("<fn>"); }
        if (arg === context.document || (arg && arg.__name === "document")) return doc;
        if (typeof arg === "string") {
            if (arg.startsWith("<")) return makeEl(arg);
            return elFor(arg);
        }
        return elFor("<obj>");
    };
    context.$.each = (items, fn) => { (items || []).forEach((it, i) => fn(i, it)); };
    context.$.map = (items, fn) => (items || []).map((it, i) => fn(it, i));
    context.$.isArray = Array.isArray;
    context.$.inArray = (v, arr) => (arr || []).indexOf(v);
    context.$.extend = (t, ...srcs) => Object.assign(t, ...srcs);
    context.$.ajax = (opts) => { ajaxCalls.push(opts); };
    context.$.active = 0;
    context.$.support = {};
    context.$.fn = {};
    context.getSelection = () => ({ toString: () => "" });

    vm.createContext(context);
    vm.runInContext(src, context);
    return { context, els, ajaxCalls, flashes, doc, elFor };
}

const { context, els, ajaxCalls, flashes, doc } = loadTable();

context.selections = [1, 2, 3];
doc.fire("#delete_selected_confirm");
let call = ajaxCalls.find((c) => String(c.url).includes("deleteselectedbooks"));
assert.ok(call, "delete ajax call issued");
call.success({
    success: false,
    results: [
        { book_id: 1, status: "succeeded" },
        { book_id: 2, status: "failed", message: "disk full" },
        { book_id: 3, status: "succeeded" },
    ],
    summary: { succeeded: 2, failed: 1, skipped: 0 },
});
let ops = els["#books-table"].__calls.filter(
    (c) => c[0] === "bootstrapTable" && typeof c[1][0] === "string");
assert.deepEqual(ops.map((c) => c[1][0]), ["uncheckAll", "refresh"],
    "selections cleared before refresh");
assert.deepEqual([...context.selections], []);
els["#books-table"].trigger("load-success.bs.table");
ops = els["#books-table"].__calls.filter(
    (c) => c[0] === "bootstrapTable" && typeof c[1][0] === "string");
assert.equal(ops[ops.length - 1][1][0], "checkBy");
assert.deepEqual([...ops[ops.length - 1][1][1].values], [2]);
assert.deepEqual([...context.selections], [2]);
let region = els["#batch-results"];
let texts = region.__children.map((c) => c.__text + c.__children.map((x) => x.__text).join("|"));
assert.ok(texts.join(" ").includes("1 failed"), "summary rendered");
assert.ok(JSON.stringify(region.__children).includes("disk full"), "reason rendered as text");
assert.equal(region.__children[0].__store["attr:role"], "alert");

context.selections = [10, 11, 12];
els["#merge_confirm"].__click.call(makeEl("#merge_confirm"), {});
call = ajaxCalls.find((c) => String(c.url).includes("ajax/mergebooks"));
assert.ok(call, "merge ajax call issued");
assert.deepEqual(JSON.parse(call.data).Merge_books, [10, 11, 12]);
call.success({
    success: false,
    results: [
        { book_id: 11, status: "succeeded" },
        { book_id: 12, status: "failed", message: "conflict" },
    ],
    summary: { succeeded: 1, failed: 1, skipped: 0 },
});
els["#books-table"].trigger("load-success.bs.table");
assert.deepEqual([...context.selections], [10, 12], "retry selection keeps target first");

context.selections = [1];
doc.fire("#delete_selected_confirm");
call = ajaxCalls[ajaxCalls.length - 1];
call.error({ responseJSON: { msg: "permission denied by server" } });
assert.ok(flashes.some(([m]) => m.includes("permission denied")), "server msg flashed");

console.log("table_js_cases: all assertions passed");
