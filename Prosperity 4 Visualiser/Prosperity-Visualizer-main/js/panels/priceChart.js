import {
  subscribe,
  getState,
  getReference,
  setTickIdx,
  setPrefs,
} from "../store.js";

const DAY_STRIDE = 1_000_000;
import { createChart } from "../chart.js";

// Default toggle state lives in prefs so the user's choice persists.
// Keys: priceLevels (L2/L3), priceBuys, priceSells, priceBots, priceOverlayDays.
//
// Products whose fair value drifts by a known amount per day. In overlay
// mode we subtract `day * offset` from prices so the days line up on a
// common y-axis instead of stepping up the chart.
const DAY_OFFSET_PER_DAY = {
  INTARIAN_PEPPER_ROOT: 1000,
};

export function mountPriceChart({
  canvasEl,
  emptyEl,
  titleEl,
  legendEl,
  levelsCheck,
  midCheck,
  microCheck,
  wallMidCheck,
  buysCheck,
  sellsCheck,
  botsCheck,
  overlayCheck,
  joinGapsCheck,
  resetZoomBtn,
}) {
  let chart = null;
  let lastKey = null;
  let currentLegend = [];

  levelsCheck.addEventListener("change", () =>
    setPrefs({ priceLevels: levelsCheck.checked })
  );
  midCheck.addEventListener("change", () =>
    setPrefs({ priceMid: midCheck.checked })
  );
  microCheck.addEventListener("change", () =>
    setPrefs({ priceMicro: microCheck.checked })
  );
  wallMidCheck.addEventListener("change", () =>
    setPrefs({ priceWallMid: wallMidCheck.checked })
  );
  buysCheck.addEventListener("change", () =>
    setPrefs({ priceBuys: buysCheck.checked })
  );
  sellsCheck.addEventListener("change", () =>
    setPrefs({ priceSells: sellsCheck.checked })
  );
  botsCheck.addEventListener("change", () =>
    setPrefs({ priceBots: botsCheck.checked })
  );
  overlayCheck.addEventListener("change", () =>
    setPrefs({ priceOverlayDays: overlayCheck.checked })
  );
  joinGapsCheck.addEventListener("change", () =>
    setPrefs({ priceJoinGaps: joinGapsCheck.checked })
  );
  resetZoomBtn.addEventListener("click", () => chart?.resetXView());

  function ensureChart() {
    if (chart) return;
    chart = createChart(canvasEl, {
      onSeek: (xValue) => {
        const state = getState();
        const ref = getReference(state);
        if (!ref) return;
        if (state.prefs.priceOverlayDays) {
          // x here is a raw timestamp (0..~999900). Jump to the nearest
          // tick within the currently-viewed day.
          const curDay = ref.days[state.tickIdx] ?? ref.days[0] ?? 0;
          let best = -1;
          let bestDelta = Infinity;
          for (let i = 0; i < ref.days.length; i++) {
            if (ref.days[i] !== curDay) continue;
            const d = Math.abs(ref.rawTimestamps[i] - xValue);
            if (d < bestDelta) {
              bestDelta = d;
              best = i;
            }
          }
          if (best >= 0) setTickIdx(best);
          return;
        }
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
      .map((s) => {
        let v = null;
        if (values && s.seriesIdx) {
          for (const idx of s.seriesIdx) {
            const candidate = values[idx];
            if (candidate != null && Number.isFinite(candidate)) {
              v = candidate;
              break;
            }
          }
        }
        const swatch = s.marker
          ? `<span class="legend-swatch marker-${s.marker}" style="background:${s.color};color:${s.color}"></span>`
          : s.dash
            ? `<span class="legend-swatch dash" style="color:${s.color}"></span>`
            : `<span class="legend-swatch" style="background:${s.color}"></span>`;
        const val =
          v == null
            ? s.marker
              ? ""
              : `<span class="legend-value muted">—</span>`
            : `<span class="legend-value">${v.toFixed(1)}</span>`;
        return `<span class="legend-row">${swatch}<span class="legend-name">${escapeHtml(s.name)}</span>${val}</span>`;
      })
      .join("");
  }

  function computeModel(state, ref, product) {
    const ps = ref.series[product];
    const overlay = !!state.prefs.priceOverlayDays;
    const dayOffset = DAY_OFFSET_PER_DAY[product] ?? 0;
    // `priceJoinGaps` defaults to true (existing behavior — connect across
    // missing samples). When false, NaNs lift the pen and leave visible gaps.
    const breakOnNaN = state.prefs.priceJoinGaps === false;

    // In overlay mode, each metric becomes N sub-series (one per day)
    // plotted against raw ts; otherwise a single series against tickKey.
    // Roots-style products get a per-day y-offset removed so the daily
    // price patterns sit on the same y range.
    const segments = buildSegments(ref, overlay);
    const makeSeries = (ys, baseProps) => {
      if (!overlay) {
        return [{ ...baseProps, breakOnNaN, xs: segments[0].xs, ys }];
      }
      const out = [];
      for (const seg of segments) {
        const raw = ys.slice(seg.start, seg.end);
        const segYs = dayOffset
          ? raw.map((v) => (Number.isFinite(v) ? v - seg.day * dayOffset : v))
          : raw;
        out.push({ ...baseProps, breakOnNaN, xs: seg.xs, ys: segYs });
      }
      return out;
    };

    const series = [];
    series.push(...makeSeries(ps.bestAsk, { name: "Best ask (L1)", color: "#f87171", width: 1.2 }));
    series.push(...makeSeries(ps.bestBid, { name: "Best bid (L1)", color: "#34d399", width: 1.2 }));

    if (state.prefs.priceLevels !== false) {
      series.push(...makeSeries(ps.askPrices?.[1] ?? [], { name: "Ask L2", color: "#f8717199", width: 1 }));
      series.push(...makeSeries(ps.askPrices?.[2] ?? [], { name: "Ask L3", color: "#f8717166", width: 1 }));
      series.push(...makeSeries(ps.bidPrices?.[1] ?? [], { name: "Bid L2", color: "#34d39999", width: 1 }));
      series.push(...makeSeries(ps.bidPrices?.[2] ?? [], { name: "Bid L3", color: "#34d39966", width: 1 }));
    }
    if (state.prefs.priceMid !== false) {
      series.push(...makeSeries(ps.midPrice, { name: "Mid", color: "#a78bfa", width: 1.6 }));
    }
    if (state.prefs.priceMicro !== false) {
      series.push(...makeSeries(ps.microPrice, { name: "Microprice", color: "#2dd4bf", width: 1.2, dash: [4, 3] }));
    }
    if (state.prefs.priceWallMid !== false) {
      series.push(...makeSeries(ps.wallMid ?? [], { name: "Wall mid", color: "#fbbf24", width: 1.2, dash: [2, 4] }));
    }

    // Markers: SUBMISSION buys (^), SUBMISSION sells (v), bot trades (·).
    const markers = [];
    const ownBuysXs = [];
    const ownBuysYs = [];
    const ownSellsXs = [];
    const ownSellsYs = [];
    const botXs = [];
    const botYs = [];
    for (const t of ref.trades) {
      if (t.symbol !== product) continue;
      if (t.price <= 0) continue; // skip backtester end-of-day reset artifacts
      // Overlay mode: x becomes raw ts and root-style prices shift down
      // by day*offset so markers land on the overlaid price line.
      const x = overlay ? t.timestamp : (t.tickKey ?? t.timestamp);
      const y = overlay && dayOffset ? t.price - (t.day ?? 0) * dayOffset : t.price;
      const isBuy = t.buyer === "SUBMISSION";
      const isSell = t.seller === "SUBMISSION";
      if (isBuy) {
        ownBuysXs.push(x);
        ownBuysYs.push(y);
      } else if (isSell) {
        ownSellsXs.push(x);
        ownSellsYs.push(y);
      } else {
        botXs.push(x);
        botYs.push(y);
      }
    }
    // Own trades get a fat, high-contrast style so they jump out of
    // the noisy line series: larger shape, dark outline, bright fill.
    if (state.prefs.priceBuys)
      markers.push({
        name: "Own buys",
        color: "#4ade80",
        outline: "#052e16",
        shape: "up",
        size: 11,
        xs: ownBuysXs,
        ys: ownBuysYs,
      });
    if (state.prefs.priceSells)
      markers.push({
        name: "Own sells",
        color: "#fb7185",
        outline: "#450a0a",
        shape: "down",
        size: 11,
        xs: ownSellsXs,
        ys: ownSellsYs,
      });
    if (state.prefs.priceBots)
      markers.push({
        name: "Bot trades",
        color: "#d4d4d8",
        outline: "#18181b",
        shape: "dot",
        size: 11,
        xs: botXs,
        ys: botYs,
      });

    // Dedupe legend by series name (overlay mode emits N sub-series per
    // metric that should collapse into one legend row).
    const legendByName = new Map();
    for (let i = 0; i < series.length; i++) {
      const s = series[i];
      const existing = legendByName.get(s.name);
      if (existing) {
        existing.seriesIdx.push(i);
      } else {
        legendByName.set(s.name, {
          name: s.name,
          color: s.color,
          dash: !!s.dash,
          seriesIdx: [i],
        });
      }
    }
    currentLegend = Array.from(legendByName.values());
    for (const mk of markers) {
      currentLegend.push({
        name: `${mk.name} (${mk.xs.length})`,
        color: mk.color,
        marker: mk.shape,
      });
    }

    return {
      xFormat: (v) => Math.round(v).toLocaleString(),
      yFormat: (v) => v.toFixed(1),
      targetPoints: state.prefs.showSampled ? 1500 : Infinity,
      series,
      markers,
    };
  }

  function render() {
    const state = getState();
    const ref = getReference(state);
    const product = state.selectedProduct ?? ref?.products[0] ?? null;
    titleEl.textContent = `Price & Liquidity ${product ? "· " + product : ""}`;

    levelsCheck.checked = state.prefs.priceLevels !== false;
    midCheck.checked = state.prefs.priceMid !== false;
    microCheck.checked = state.prefs.priceMicro !== false;
    wallMidCheck.checked = state.prefs.priceWallMid !== false;
    buysCheck.checked = !!state.prefs.priceBuys;
    sellsCheck.checked = !!state.prefs.priceSells;
    botsCheck.checked = !!state.prefs.priceBots;
    overlayCheck.checked = !!state.prefs.priceOverlayDays;
    joinGapsCheck.checked = state.prefs.priceJoinGaps !== false;

    if (!ref || !product) {
      if (chart) {
        chart.destroy();
        chart = null;
      }
      currentLegend = [];
      legendEl.innerHTML = "";
      emptyEl.textContent = ref ? "Select a product." : "Load a log to see prices.";
      emptyEl.classList.remove("hidden");
      canvasEl.classList.add("hidden");
      return;
    }
    emptyEl.classList.add("hidden");
    canvasEl.classList.remove("hidden");
    ensureChart();

    const dayFilter = state.activeDayFilter;
    const key = [
      ref.id,
      product,
      state.prefs.showSampled,
      state.prefs.priceLevels !== false,
      state.prefs.priceMid !== false,
      state.prefs.priceMicro !== false,
      state.prefs.priceWallMid !== false,
      !!state.prefs.priceBuys,
      !!state.prefs.priceSells,
      !!state.prefs.priceBots,
      !!state.prefs.priceOverlayDays,
      state.prefs.priceJoinGaps !== false,
      dayFilter ?? "all",
    ].join("|");
    if (key !== lastKey) {
      chart.setData(computeModel(state, ref, product));
      lastKey = key;
      renderLegend(null);
      // In overlay mode the X axis is raw timestamp (0..~999900) — day
      // filter zoom doesn't apply since every day is already visible there.
      if (!state.prefs.priceOverlayDays) {
        if (dayFilter !== null) {
          chart.setXView(dayFilter * DAY_STRIDE, (dayFilter + 1) * DAY_STRIDE);
        } else {
          chart.resetXView();
        }
      }
    }
    const cursorX = state.prefs.priceOverlayDays
      ? ref.rawTimestamps[state.tickIdx] ?? 0
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

/**
 * Split the flat per-tick arrays into per-day segments (for overlay mode)
 * or return a single segment spanning everything (for side-by-side mode).
 * Each segment carries its own xs array already built from either raw ts
 * (overlay) or tickKey (side-by-side), so the caller can plot directly.
 */
function buildSegments(ref, overlay) {
  const days = ref.days ?? [];
  const rawTs = ref.rawTimestamps ?? [];
  const tickKeys = ref.timestamps ?? [];
  const len = tickKeys.length;
  if (!overlay || len === 0) {
    return [{ day: days[0] ?? 0, start: 0, end: len, xs: tickKeys }];
  }
  const segs = [];
  let start = 0;
  for (let i = 1; i <= len; i++) {
    if (i === len || days[i] !== days[start]) {
      segs.push({
        day: days[start] ?? 0,
        start,
        end: i,
        xs: rawTs.slice(start, i),
      });
      start = i;
    }
  }
  return segs;
}

