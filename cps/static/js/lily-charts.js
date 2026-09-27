/*
 * Lily charts: an ECharts theme built from the design tokens in lily.css.
 * Used only by the stats pages (cwa_stats_tabs.html). No colour is written here; every colour is read
 * from a CSS custom property at runtime, so the charts follow ~/projects/DESIGN.md and the Lily palette.
 *
 *   LilyCharts.init(elOrId)        echarts.init with the "lily" theme
 *   LilyCharts.t                   resolved tokens: paper, surface, ink, inkSoft, muted, faint, line, lineSoft,
 *                                  accent, success, warning, danger, accentSoft, controlTint
 *   LilyCharts.series(i)           categorical slot i (fixed order, never cycled past the last slot)
 *   LilyCharts.mix(token, pct, base)  a token at pct% into base ("transparent", "var(--surface)", ...)
 *   LilyCharts.ramp()              sequential ramp for magnitude (heatmaps): accent, light to dark
 *   LilyCharts.empty(chart, text)  a "no data" message in muted text
 *   LilyCharts.barItem(horizontal) 4px rounded data end, square at the baseline
 */
(function () {
    'use strict';

    var probe = null;
    var cache = {};

    function toRgb(str) {
        // Chrome serialises color-mix() as color(srgb r g b / a); make that something ECharts can parse.
        var m = /color\(srgb\s+([\d.e-]+)\s+([\d.e-]+)\s+([\d.e-]+)(?:\s*\/\s*([\d.e-]+))?\)/.exec(str);
        if (!m) return str;
        var c = function (v) { return Math.round(Math.max(0, Math.min(1, parseFloat(v))) * 255); };
        var a = m[4] === undefined ? 1 : parseFloat(m[4]);
        return 'rgba(' + c(m[1]) + ', ' + c(m[2]) + ', ' + c(m[3]) + ', ' + a + ')';
    }

    function resolve(expr) {
        if (cache[expr]) return cache[expr];
        if (!probe) {
            probe = document.createElement('span');
            probe.setAttribute('aria-hidden', 'true');
            probe.style.display = 'none';
            document.body.appendChild(probe);
        }
        probe.style.color = '';
        probe.style.color = expr;
        var out = toRgb(getComputedStyle(probe).color);
        cache[expr] = out;
        return out;
    }

    function token(name) { return resolve('var(--' + name + ')'); }

    function mix(name, pct, base) {
        return resolve('color-mix(in srgb, var(--' + name + ') ' + pct + '%, ' + (base || 'transparent') + ')');
    }

    var tokens = null;
    function t() {
        if (tokens) return tokens;
        tokens = {
            paper: token('paper'),
            surface: token('surface'),
            ink: token('ink'),
            inkSoft: token('ink-soft'),
            muted: token('muted'),
            faint: token('faint'),
            line: token('line'),
            lineSoft: token('line-soft'),
            accent: token('accent'),
            success: token('success'),
            warning: token('warning'),
            danger: token('danger'),
            accentSoft: mix('accent', 12, 'var(--surface)'),
            controlTint: mix('ink', 6, 'var(--surface)'),
            fontUi: getComputedStyle(document.body).getPropertyValue('--font-ui').trim() || 'sans-serif',
            fontMono: getComputedStyle(document.body).getPropertyValue('--font-mono').trim() || 'monospace'
        };
        return tokens;
    }

    // Categorical order. --series-N come from lily.css when defined; until then the status-free
    // fallbacks keep every series on a Lily token.
    var SERIES_FALLBACK = ['accent', 'ink-soft', 'success', 'warning', 'faint'];
    var palette = null;
    function seriesPalette() {
        if (palette) return palette;
        palette = SERIES_FALLBACK.map(function (fb, i) {
            return resolve('var(--series-' + (i + 1) + ', var(--' + fb + '))');
        });
        return palette;
    }

    function series(i) {
        var p = seriesPalette();
        return p[Math.min(i, p.length - 1)];
    }

    function ramp() {
        return [mix('accent', 8, 'var(--surface)'), mix('accent', 35, 'var(--surface)'),
                mix('accent', 70, 'var(--surface)'), t().accent];
    }

    function theme() {
        var k = t();
        var axisCommon = {
            axisLine: { show: true, lineStyle: { color: k.line, width: 1 } },
            axisTick: { show: false },
            axisLabel: { color: k.muted, fontSize: 11, fontFamily: k.fontUi },
            nameTextStyle: { color: k.muted, fontSize: 11 },
            splitLine: { show: false, lineStyle: { color: k.lineSoft, width: 1, type: 'solid' } },
            splitArea: { show: false }
        };
        var valueAxis = JSON.parse(JSON.stringify(axisCommon));
        valueAxis.axisLine.show = false;
        valueAxis.splitLine.show = true;
        return {
            color: seriesPalette(),
            backgroundColor: 'transparent',
            textStyle: { fontFamily: k.fontUi, color: k.muted, fontSize: 11 },
            title: {
                textStyle: { color: k.muted, fontFamily: k.fontUi, fontSize: 13, fontWeight: 400 },
                subtextStyle: { color: k.faint }
            },
            legend: {
                textStyle: { color: k.inkSoft, fontSize: 11, fontFamily: k.fontUi },
                itemWidth: 10, itemHeight: 10, itemGap: 14, icon: 'roundRect',
                inactiveColor: k.line,
                pageIconColor: k.muted, pageIconInactiveColor: k.line, pageTextStyle: { color: k.muted }
            },
            tooltip: {
                backgroundColor: k.surface,
                borderColor: k.line,
                borderWidth: 1,
                padding: [6, 10],
                textStyle: { color: k.ink, fontSize: 12, fontFamily: k.fontUi },
                extraCssText: 'border-radius:10px;box-shadow:var(--menu-shadow);',
                axisPointer: {
                    lineStyle: { color: k.line, width: 1 },
                    crossStyle: { color: k.line, width: 1 },
                    shadowStyle: { color: k.controlTint },
                    label: { backgroundColor: k.inkSoft, color: k.paper }
                }
            },
            categoryAxis: axisCommon,
            valueAxis: valueAxis,
            timeAxis: axisCommon,
            logAxis: valueAxis,
            line: { symbol: 'circle', symbolSize: 6, showSymbol: false, smooth: false, lineStyle: { width: 2 } },
            bar: { barMaxWidth: 24, itemStyle: { borderRadius: [4, 4, 0, 0] } },
            pie: { itemStyle: { borderColor: k.surface, borderWidth: 2 }, label: { color: k.inkSoft } },
            gauge: { axisLine: { lineStyle: { color: [[1, k.controlTint]] } } },
            sankey: { itemStyle: { borderWidth: 0 }, lineStyle: { color: 'source', opacity: 0.25 }, label: { color: k.inkSoft } },
            treemap: { itemStyle: { borderColor: k.surface, borderWidth: 2, gapWidth: 2 } },
            visualMap: { color: ramp().slice().reverse(), textStyle: { color: k.muted } },
            dataZoom: {
                borderColor: k.line, fillerColor: k.accentSoft, handleColor: k.accent,
                textStyle: { color: k.muted }, dataBackgroundColor: k.line
            }
        };
    }

    var registered = false;
    function init(el) {
        if (typeof el === 'string') el = document.getElementById(el);
        if (!registered) {
            echarts.registerTheme('lily', theme());
            registered = true;
        }
        return echarts.init(el, 'lily');
    }

    function empty(chart, text) {
        chart.setOption({
            title: {
                text: text || 'Nothing to show yet',
                left: 'center', top: 'center',
                textStyle: { color: t().muted, fontSize: 13, fontWeight: 400 }
            },
            xAxis: { show: false }, yAxis: { show: false }, series: []
        }, true);
    }

    function barItem(horizontal) {
        return { borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] };
    }

    window.LilyCharts = {
        init: init,
        get t() { return t(); },
        series: series,
        get palette() { return seriesPalette().slice(); },
        mix: mix,
        ramp: ramp,
        empty: empty,
        barItem: barItem,
        token: token
    };
})();
