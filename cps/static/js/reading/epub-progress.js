/* global reader, ePub, calibre, LilyProgress */

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
    enabled: calibre.syncProgress === true
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
    progressSync.save(start.cfi, fraction);
}

window.addEventListener('locationchange',()=>{
    let newPos=calculateProgress();
    if (progressDiv) {
        progressDiv.textContent=newPos+"%";
    }
    saveProgress();
});

var epub=ePub(calibre.bookUrl)

let progressDiv=document.getElementById("progress");

/** Pre-sync builds kept an integer percentage under this key; read it once as a fallback. */
function legacyLocalPercent(){
    try {
        let saved = localStorage.getItem("calibre.reader.progress." + calibre.bookUrl);
        let percent = parseInt(saved, 10);
        return isNaN(percent) ? null : percent / 100;
    } catch (e) {
        return null;
    }
}

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
            let restored = displayPosition(saved);
            if (!restored) {
                let legacy = legacyLocalPercent();
                if (legacy !== null) {
                    restored = displayPosition({cfi: "", percent: legacy});
                }
            }
        }
        progressRestored = true;
        window.dispatchEvent(new Event('locationchange'))
    }).catch(()=>{
        progressRestored = true;
    });
})

if (reader && reader.rendition && typeof reader.rendition.on === "function") {
    // History pushes are skipped when the hash already matches, so listen here as well.
    reader.rendition.on("relocated", saveProgress);
}
