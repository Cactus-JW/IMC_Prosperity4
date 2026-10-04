import {
  subscribe,
  getState,
  getReference,
  setPositionLimit,
  setTickIdx,
} from "../store.js";

const DAY_STRIDE = 1_000_000;
import { createChart } from "../chart.js";

export function mountPositionChart({
  canvasEl,
  emptyEl,
  titleEl,
  legendEl,
  limitInput,
  resetZoomBtn,
}) {
  let chart = null;
  let lastKey = null;
  let currentLegend = [];

  limitInput.addEventListener("change", (e) => {
    const state = getState();
    const ref = getReference(state);
    const product = state.selectedProduct ?? ref?.products[0] ?? null;
    if (!ref || !product) return;
    const v = Math.max(1, Number(e.target.value) || 1);
    setPositionLimit(ref.id, product, v);
  });
  resetZoomBtn.addEventListener("click", () => chart?.resetXView());

  function ensureChart() {
    if (chart) return;
    chart = createChart(canvasEl, {
      onSeek: (xValue) => {
        const state = getState();
        const ref = getReference(state);
        if (!ref) return;
        const ts = ref.timestamps;
        if (ts.length < 2) return;
        let lo = 0;
        let hi = ts.length - 1;
        while (lo < hi) {
          const mid = (lo + hi) >>> 1;
          if (ts[mid] < xValue) lo = mid + 1;
          else hi = mid;
        }
        setTickIdx(lo);
      },
      onHover: renderLegend,
    });
  }

  function renderLegend(values) {
    if (!currentLegend.length) {
      legendEl.innerHTML = "";
      return;
    }
    legendEl.innerHTML = currentLegend
      .map((s, i) => {
        const v = values ? values[i] : null;
        const val =
          v == null
            ? `<span class="legend-value muted">—</span>`
            : `<span class="legend-value">${v.toFixed(0)}</span>`;
        return `<span class="legend-row">
          <span class="legend-swatch" style="background:${s.color}"></span>
          <span class="legend-name">${escapeHtml(s.name)}</span>${val}
        </span>`;
      })
      .join("");
  }

  function computeModel(state, ref, product, limit) {
    const { strategies, comparingIds, activeDayFilter } = state;
    const series = [];

    // When a day is selected, find the position at the start of that day so we
    // can display relative intra-day movement (starting from 0) instead of the
    // absolute running position (which looks flat when holdings don't change).
    function dayStartPos(s) {
      if (activeDayFilter === null || !s.series[product]) return 0;
      const idx = s.days.indexOf(activeDayFilter);
      return idx >= 0 ? s.series[product].position[idx] : 0;
    }

    function add(s) {
      if (!s.series[product]) return;
      const xs = s.timestamps;
      const raw = s.series[product].position;
      const startPos = dayStartPos(s);
      const ys =
        startPos === 0
          ? raw
          : raw.map((v) => v - startPos);
      series.push({
        name: s.name,
        color: s.color,
        xs,
        ys,
        width: s.id === ref.id ? 2 : 1,
      });
    }
    add(ref);
    for (const s of strategies) {
      if (s.id === ref.id || !comparingIds.has(s.id)) continue;
      add(s);
    }
    currentLegend = series.map((s) => ({ name: s.name, color: s.color }));

    // Shift limit lines by the reference start position so they correctly show
    // the absolute constraints in relative-position space.
    const refStartPos = dayStartPos(ref);
    return {
      xFormat: (v) => Math.round(v).toLocaleString(),
      yFormat: (v) => v.toFixed(0),
      targetPoints: state.prefs.showSampled ? 1200 : Infinity,
      series,
      limitLines: [
        { value: limit - refStartPos, color: "rgba(244,63,94,0.6)", dash: [3, 3] },
        { value: -limit - refStartPos, color: "rgba(244,63,94,0.6)", dash: [3, 3] },
      ],
    };
  }

  function render() {
    const state = getState();
    const ref = getReference(state);
    const product = state.selectedProduct ?? ref?.products[0] ?? null;
    titleEl.textContent = `Position ${product ? "· " + product : ""}`;

    if (!ref || !product) {
      if (chart) {
        chart.destroy();
        chart = null;
      }
      currentLegend = [];
      legendEl.innerHTML = "";
      emptyEl.textContent = ref ? "Select a product." : "Load a log to see positions.";
      emptyEl.classList.remove("hidden");
      canvasEl.classList.add("hidden");
      limitInput.disabled = true;
      return;
    }
    emptyEl.classList.add("hidden");
    canvasEl.classList.remove("hidden");
    limitInput.disabled = false;

    const limit = ref.positionLimits[product] ?? 50;
    if (document.activeElement !== limitInput) limitInput.value = String(limit);

    ensureChart();

    const dayFilter = state.activeDayFilter;
    const key = [
      ref.id,
      product,
      state.prefs.showSampled,
      Array.from(state.comparingIds).join(","),
      state.strategies.length,
      limit,
      dayFilter ?? "all",
    ].join("|");
    if (key !== lastKey) {
      chart.setData(computeModel(state, ref, product, limit));
      lastKey = key;
      renderLegend(null);
      if (dayFilter !== null) {
        chart.setXView(dayFilter * DAY_STRIDE, (dayFilter + 1) * DAY_STRIDE);
      } else {
        chart.resetXView();
      }
    }
    chart.setCursorX(ref.timestamps[state.tickIdx] ?? 0);
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
