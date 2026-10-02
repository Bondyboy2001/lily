/* global reader, calibre, LilyProgress */

/**
 * waits until queue is finished, meaning the book is done loading
 * @param callback
 */
function qFinished(callback){
    let timeout=setInterval(()=>{
        if (reader && reader.rendition && reader.rendition.q && reader.rendition.q.running === undefined) {
            clearInterval(timeout);
            callback();
        }
        },300
    )
}

/** Fraction (0..1) of the book read at the current location, or null before locations exist. */
function currentFraction(){
    if (!reader || !reader.rendition || !reader.rendition.location || !reader.rendition.location.end) {
        return null;
    }
    let data=reader.rendition.location.end;
    if (!data || !data.cfi || !epub || !epub.locations) {
        return null;
    }
    let fraction = epub.locations.percentageFromCfi(data.cfi);
    return typeof fraction === "number" && !isNaN(fraction) ? fraction : null;
}

// Sections after the story that a reader rarely pages through. Reaching the last page before them
// counts as finishing the book, so long endnotes or an index don't leave it stuck at 90%.
const BACK_MATTER = /^\s*(notes|endnotes|index|bibliography|references|works cited|sources|acknowledg|about the (author|authors|publisher)|also by|other (books|titles) by|by the same author|glossary|appendix|appendices|copyright|credits|colophon|further reading|reading group|discussion questions|permissions)/i;
const STORY_END_MIN_FRACTION = 0.9;

function hrefKey(href){
    return String(href || "").split("#")[0].split("/").pop();
}

function tocLabels(){
    let labels = {};
    let walk = (items)=>{
        (items || []).forEach((item)=>{
            let key = hrefKey(item.href);
            if (key && !(key in labels)) {
                labels[key] = String(item.label || "").trim();
            }
            walk(item.subitems);
        });
    };
    walk(epub && epub.navigation ? epub.navigation.toc : []);
    return labels;
}

/** True on the last page of the story: the book's last page, or the last page of a section that
 *  only back matter (by its table-of-contents label) follows, past 90% of the book. */
function atStoryEnd(fraction){
    let location = reader.rendition.location;
    if (location.atEnd) {
        return true;
    }
    let end = location.end;
    if (fraction < STORY_END_MIN_FRACTION || !end || !end.displayed || end.displayed.page < end.displayed.total
        || !epub.spine || !epub.spine.spineItems) {
        return false;
    }
    let labels = tocLabels();
    let later = epub.spine.spineItems.slice(end.index + 1).filter((item)=>item.linear !== false && item.linear !== "no");
    return later.every((item)=>{
        let label = labels[hrefKey(item.href)];
        return !label || BACK_MATTER.test(label);
    });
}

function calculateProgress(){
    let fraction = currentFraction();
    return fraction === null ? 0 : Math.round(fraction*100);
}

// register new event emitter locationchange that fires on urlchange
// source: https://stackoverflow.com/a/52809105/21941129
(() => {
    let oldPushState = history.pushState;
    history.pushState = function pushState() {
        let ret = oldPushState.apply(this, arguments);
        window.dispatchEvent(new Event('locationchange'));
        return ret;
    };

    let oldReplaceState = history.replaceState;
    history.replaceState = function replaceState() {
        let ret = oldReplaceState.apply(this, arguments);
        window.dispatchEvent(new Event('locationchange'));
        return ret;
    };

    window.addEventListener('popstate', () => {
        window.dispatchEvent(new Event('locationchange'));
    });
})();

// Position sync: localStorage for this device, /ajax/progress/<id> for every other one.
const progressSync = LilyProgress.create({
    url: calibre.progressUrl,
    storageKey: calibre.progressKey || calibre.bookUrl,
    format: calibre.progressFormat,
    statusEl: document.getElementById("progress-sync-status"),
    enabled: calibre.syncProgress === true,
    // The book's length in pages, for how long it takes to read: epub.js makes a location
    // every 150 characters, and a printed page holds about 1,800
    pages: () => {
        let count = epub && epub.locations ? epub.locations.length() : 0;
        return count ? count * 150 / 1800 : null;
    }
});
// Nothing is saved until the starting position has been restored, otherwise the
// first page render would overwrite the position we are about to jump to.
let progressRestored = false;

function saveProgress(){
    if (!progressRestored || !reader || !reader.rendition || !reader.rendition.location) {
        return;
    }
    let start = reader.rendition.location.start;
    let fraction = currentFraction();
    if (!start || !start.cfi || fraction === null) {
        return;
    }
    progressSync.save(start.cfi, atStoryEnd(fraction) ? 1 : fraction);
}

function showProgress(){
    // Blank until the locations exist, rather than a misleading 0%.
    if (progressDiv) {
        progressDiv.textContent=currentFraction()===null ? "" : calculateProgress()+"%";
    }
}

window.addEventListener('locationchange',()=>{
    showProgress();
    saveProgress();
});

// The reader's own book: a second ePub() here would download and parse the file again.
var epub=reader.book;

let progressDiv=document.getElementById("progress");

function displayPosition(pos){
    if (!pos) {
        return false;
    }
    if (pos.cfi && pos.cfi.indexOf("epubcfi(") === 0) {
        reader.rendition.display(pos.cfi);
        return true;
    }
    if (pos.percent !== null && pos.percent !== undefined && pos.percent > 0) {
        let cfi = epub.locations.cfiFromPercentage(pos.percent);
        if (cfi) {
            reader.rendition.display(cfi);
            return true;
        }
    }
    return false;
}

qFinished(()=>{
    if (!epub || !epub.locations) {
        return;
    }
    Promise.all([epub.locations.generate(), progressSync.load()]).then(([, saved])=> {
        if (reader && reader.rendition) {
            displayPosition(saved);
        }
        progressRestored = true;
        window.dispatchEvent(new Event('locationchange'))
    }).catch(()=>{
        progressRestored = true;
    });
})

if (reader && reader.rendition && typeof reader.rendition.on === "function") {
    // History pushes are skipped when the hash already matches, so listen here as well.
    reader.rendition.on("relocated", ()=>{
        showProgress();
        saveProgress();
    });
}
