import {
  subscribe,
  getState,
  getReference,
  setPrefs,
  setTickIdx,
} from "../store.js";

const DAY_STRIDE = 1_000_000;
import { createChart } from "../chart.js";
import { downloadCanvasPng } from "../exporters.js";

const TARGET_POINTS = 1500;

export function mountPnlChart({
  canvasEl,
  emptyEl,
  legendEl,
  normCheck,
  diffCheck,
  sampledCheck,
  exportBtn,
  resetZoomBtn,
}) {
  let chart = null;
  let lastKey = null;
  let currentSeriesForLegend = [];

  normCheck.addEventListener("change", () =>
    setPrefs({ normalizedX: normCheck.checked })
  );
  diffCheck.addEventListener("change", () =>
    setPrefs({ diffMode: diffCheck.checked })
  );
  sampledCheck.addEventListener("change", () =>
    setPrefs({ showSampled: sampledCheck.checked })
  );
  exportBtn.addEventListener("click", () => {
    downloadCanvasPng(canvasEl, "pnl-performance.png");
  });
  resetZoomBtn.addEventListener("click", () => chart?.resetXView());

  function ensureChart() {
    if (chart) return;
    chart = createChart(canvasEl, {
      onSeek: (xValue) => {
        const state = getState();
        const ref = getReference(state);
        if (!ref) return;
        const len = ref.timestamps.length;
        if (len < 2) return;
        if (state.prefs.normalizedX) {
          setTickIdx(Math.round(xValue * (len - 1)));
        } else {
          // nearest tickKey
          let lo = 0;
          let hi = len - 1;
          while (lo < hi) {
            const mid = (lo + hi) >>> 1;
            if (ref.timestamps[mid] < xValue) lo = mid + 1;
            else hi = mid;
          }
          setTickIdx(lo);
        }
      },
      onHover: (values, cursor) => {
        renderLegend(values, cursor);
      },
    });
  }

  function renderLegend(values, cursor) {
    if (!currentSeriesForLegend.length) {
      legendEl.innerHTML = "";
      return;
    }
    legendEl.innerHTML = currentSeriesForLegend
      .map((s, i) => {
        const v = values ? values[i] : null;
        const cls =
          v === null ? "muted" : v >= 0 ? "positive" : "negative";
        const text =
          v === null
            ? "—"
            : (v >= 0 ? "+" : "") +
              (Math.abs(v) >= 1000
                ? (v / 1000).toFixed(1) + "k"
                : v.toFixed(0));
        return `<span class="legend-row">
          <span class="legend-swatch" style="background:${s.color}"></span>
          <span class="legend-name">${escapeHtml(s.name)}</span>
          <span class="legend-value ${cls}">${text}</span>
        </span>`;
      })
      .join("");
    void cursor;
  }

  function pnlSeriesFor(strat, selectedProduct) {
    if (!selectedProduct || !strat.series[selectedProduct]) {
      return strat.totalPnl;
    }
    const src = strat.series[selectedProduct].pnl;
    const out = new Array(src.length);
    let last = 0;
    for (let i = 0; i < src.length; i++) {
      if (Number.isFinite(src[i])) last = src[i];
      out[i] = last;
    }
    return out;
  }

  // Accumulate per-day PnL into a running total across days.
  // The activities log resets PnL to 0 at the start of each day (default
  // backtester behaviour). This carries each previous day's final value
  // forward so the all-days view shows a continuous, non-resetting curve.
  // If the log was generated with --merge-pnl the data is already cumulative;
  // in that case the new-day start won't be below the previous-day end, so
  // no offset is applied and the function is a safe no-op.
  function makeCumulativePnl(ys, days) {
    const out = new Array(ys.length);
    let offset = 0;
    for (let i = 0; i < ys.length; i++) {
      if (i > 0 && days[i] !== days[i - 1]) {
        const prevEnd = ys[i - 1];
        const newStart = ys[i];
        // Add offset when the raw PnL resets (backtester always resets to 0).
        // For already-merged logs newStart ≈ prevEnd so delta ≈ 0 — safe no-op.
        const delta = prevEnd - newStart;
        if (delta !== 0) {
          offset += delta;
        }
      }
      out[i] = ys[i] + offset;
    }
    return out;
  }

  function computeModel(state) {
    const ref = getReference(state);
    if (!ref) return null;
    const { prefs, strategies, comparingIds, selectedProduct, activeDayFilter } = state;
    const compareList = strategies.filter(
      (s) => comparingIds.has(s.id) && s.id !== ref.id
    );
    const refXs = ref.timestamps;
    // All-days view: cumulative PnL so each day continues from where the last
    // ended.  Day-filtered view: per-day PnL starting at 0 (current behaviour).
    let refYs = pnlSeriesFor(ref, selectedProduct);
    if (activeDayFilter === null) {
      refYs = makeCumulativePnl(refYs, ref.days);
    }

    function project(strat) {
      const xsBase = prefs.normalizedX
        ? strat.timestamps.map((_, i) =>
            strat.timestamps.length > 1 ? i / (strat.timestamps.length - 1) : 0
          )
        : strat.timestamps;
      let ys = pnlSeriesFor(strat, selectedProduct);
      if (activeDayFilter === null) {
        ys = makeCumulativePnl(ys, strat.days);
      }
      if (prefs.diffMode && strat.id !== ref.id) {
        const out = new Array(xsBase.length);
        if (prefs.normalizedX) {
          for (let i = 0; i < xsBase.length; i++) {
            const refIdx = Math.min(
              refXs.length - 1,
              Math.round(xsBase[i] * (refXs.length - 1))
            );
            out[i] = ys[i] - refYs[refIdx];
          }
        } else {
          let j = 0;
          for (let i = 0; i < xsBase.length; i++) {
            while (j + 1 < refXs.length && refXs[j + 1] <= xsBase[i]) j++;
            out[i] = ys[i] - refYs[j];
          }
        }
        ys = out;
      }
      return { xs: xsBase, ys };
    }

    const series = [
      { name: ref.name + " (ref)", color: ref.color, width: 2.2, ...project(ref) },
    ];
    for (const s of compareList) {
      series.push({ name: s.name, color: s.color, width: 1.2, ...project(s) });
    }
    currentSeriesForLegend = series.map((s) => ({ name: s.name, color: s.color }));

    return {
      xFormat: (v) =>
        prefs.normalizedX
          ? (v * 100).toFixed(1) + "%"
          : Math.round(v).toLocaleString(),
      yFormat: (v) =>
        Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "k" : v.toFixed(0),
      targetPoints: prefs.showSampled ? TARGET_POINTS : Infinity,
      series,
    };
  }

  function render() {
    const state = getState();
    const ref = getReference(state);

    normCheck.checked = state.prefs.normalizedX;
    diffCheck.checked = state.prefs.diffMode;
    sampledCheck.checked = state.prefs.showSampled;

    if (!ref) {
      if (chart) {
        chart.destroy();
        chart = null;
      }
      currentSeriesForLegend = [];
      legendEl.innerHTML = "";
      emptyEl.classList.remove("hidden");
      canvasEl.classList.add("hidden");
      return;
    }
    emptyEl.classList.add("hidden");
    canvasEl.classList.remove("hidden");
    ensureChart();

    const dayFilter = state.activeDayFilter;
    const key =
      state.strategies.length +
      "|" +
      state.referenceId +
      "|" +
      Array.from(state.comparingIds).join(",") +
      "|" +
      state.prefs.diffMode +
      "|" +
      state.prefs.normalizedX +
      "|" +
      state.prefs.showSampled +
      "|" +
      (state.selectedProduct ?? "") +
      "|" +
      (dayFilter ?? "all");
    if (key !== lastKey) {
      chart.setData(computeModel(state));
      lastKey = key;
      renderLegend(null, null);
      if (dayFilter !== null) {
        if (state.prefs.normalizedX) {
          // Map day tick indices to 0-1 normalized range.
          const days = ref.days;
          const len = days.length;
          let firstIdx = 0;
          let lastIdx = len - 1;
          for (let i = 0; i < len; i++) {
            if (days[i] === dayFilter) { firstIdx = i; break; }
          }
          for (let i = len - 1; i >= 0; i--) {
            if (days[i] === dayFilter) { lastIdx = i; break; }
          }
          const normMin = len > 1 ? firstIdx / (len - 1) : 0;
          const normMax = len > 1 ? lastIdx / (len - 1) : 1;
          chart.setXView(normMin, normMax);
        } else {
          chart.setXView(dayFilter * DAY_STRIDE, (dayFilter + 1) * DAY_STRIDE);
        }
      } else {
        chart.resetXView();
      }
    }

    const cursorX = state.prefs.normalizedX
      ? ref.timestamps.length > 1
        ? state.tickIdx / (ref.timestamps.length - 1)
        : 0
      : ref.timestamps[state.tickIdx] ?? 0;
    chart.setCursorX(cursorX);
  }

  subscribe(render);
  render();
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
