/**
 * Vanilla Canvas line chart with click-drag horizontal zoom, double-click
 * seek, and an optional external legend hook.
 *
 * Usage:
 *   const chart = createChart(canvas, {
 *     onSeek: (xValue) => {...},
 *     onHover: (values, xValue) => {...},   // also fires on chart-internal
 *                                            // re-renders (e.g. after zoom)
 *   });
 *   chart.setData({
 *     xFormat, yFormat,
 *     targetPoints,                // optional — per-render LTTB target for
 *                                  //   each series' visible slice (default
 *                                  //   2000). Pass Infinity to disable
 *                                  //   downsampling (draw every point).
 *     series: [{ name, color, xs, ys, width?, dash? }],
 *     limitLines: [{ value, color, dash? }],
 *   });
 *   chart.setCursorX(x);   // programmatic crosshair
 *   chart.setXView(min, max);    // zoom to a range
 *   chart.resetXView();          // back to full
 */

import { lttb } from "./downsample.js";

const DPR = window.devicePixelRatio || 1;
const DEFAULT_TARGET_POINTS = 2000;
const DRAG_THRESHOLD = 4; // px before a drag is treated as a zoom

export function createChart(canvas, opts = {}) {
  const ctx = canvas.getContext("2d");
  const { onSeek = null, onHover = null } = opts;

  let model = null;
  let cursorX = null;
  let hoverLogicalX = null;
  let lastSize = { w: 0, h: 0 };
  let resizeRaf = null;

  let plot = { left: 48, top: 8, width: 0, height: 0 };
  let xView = null; // [min, max] or null for auto-range

  const AXIS_COLOR = "#71717a";
  const GRID_COLOR = "#27272a";
  const MUTED_TEXT = "#a1a1aa";
  const ZOOM_BG = "rgba(45, 212, 191, 0.15)";
  const ZOOM_EDGE = "rgba(45, 212, 191, 0.6)";

  // --- drag zoom state ---
  let dragStart = null; // { pxStart, pxCur, started }
  let dragClickTime = 0;

  const ro = new ResizeObserver(() => {
    if (resizeRaf != null) return;
    resizeRaf = requestAnimationFrame(() => {
      resizeRaf = null;
      render();
    });
  });
  ro.observe(canvas);

  canvas.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < plot.left || px > plot.left + plot.width) return;
    dragStart = { pxStart: px, pxCur: px, started: false };
    dragClickTime = performance.now();
    e.preventDefault();
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragStart) return;
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    dragStart.pxCur = px;
    if (
      !dragStart.started &&
      Math.abs(px - dragStart.pxStart) >= DRAG_THRESHOLD
    ) {
      dragStart.started = true;
    }
    if (dragStart.started) render();
  });

  window.addEventListener("mouseup", (e) => {
    if (!dragStart) return;
    const drag = dragStart;
    dragStart = null;
    if (!drag.started) {
      render();
      return;
    }
    if (!model) {
      render();
      return;
    }
    const xmin = visibleXMin();
    const xmax = visibleXMax();
    const p2v = (px) =>
      xmin + ((px - plot.left) / plot.width) * (xmax - xmin);
    let a = p2v(Math.max(plot.left, Math.min(plot.left + plot.width, drag.pxStart)));
    let b = p2v(Math.max(plot.left, Math.min(plot.left + plot.width, drag.pxCur)));
    if (a > b) [a, b] = [b, a];
    if (b - a > (xmax - xmin) * 0.001) {
      xView = [a, b];
      render();
    } else {
      render();
    }
  });

  canvas.addEventListener("dblclick", (e) => {
    if (!model || !onSeek) return;
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < plot.left || px > plot.left + plot.width) return;
    const xmin = visibleXMin();
    const xmax = visibleXMax();
    const xValue = xmin + ((px - plot.left) / plot.width) * (xmax - xmin);
    onSeek(xValue);
  });

  // Right-click to reset zoom.
  canvas.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (xView !== null) {
      xView = null;
      render();
    }
  });

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < plot.left || px > plot.left + plot.width) {
      if (hoverLogicalX !== null) {
        hoverLogicalX = null;
        render();
      }
      return;
    }
    if (!model || !model.series.length) return;
    const xmin = visibleXMin();
    const xmax = visibleXMax();
    hoverLogicalX = xmin + ((px - plot.left) / plot.width) * (xmax - xmin);
    render();
  });

  canvas.addEventListener("mouseleave", () => {
    hoverLogicalX = null;
    render();
  });

  canvas.style.cursor = "crosshair";

  function sizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(100, Math.round(rect.width));
    const h = Math.max(80, Math.round(rect.height));
    if (w !== lastSize.w || h !== lastSize.h) {
      canvas.width = Math.round(w * DPR);
      canvas.height = Math.round(h * DPR);
      lastSize = { w, h };
    }
    return { w, h };
  }

  function visibleXMin() {
    return xView ? xView[0] : model._xmin;
  }
  function visibleXMax() {
    return xView ? xView[1] : model._xmax;
  }

  // Binary search: leftmost index with arr[i] >= target.
  function lowerBound(arr, target) {
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (arr[mid] < target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  // Visible index range in a series' xs, extended by one on each side so
  // the polyline leaves the viewport naturally. Assumes xs is sorted.
  function visibleIndexRange(xs, visXmin, visXmax) {
    const len = xs.length;
    if (!len) return [0, 0];
    let i0 = lowerBound(xs, visXmin);
    let i1 = lowerBound(xs, visXmax);
    if (i0 > 0) i0--;
    if (i1 < len) i1++;
    return [i0, i1];
  }

  function computeBounds() {
    const visXmin = visibleXMin();
    const visXmax = visibleXMax();

    // y bounds only over currently visible x range. Use binary search to
    // restrict the scan — xs are monotonic so this stays cheap even for
    // raw (un-downsampled) datasets passed in by callers.
    let ymin = Infinity,
      ymax = -Infinity;
    for (const s of model.series) {
      const xs = s.xs;
      const ys = s.ys;
      const len = Math.min(xs.length, ys.length);
      if (!len) continue;
      const [i0, i1] = visibleIndexRange(xs, visXmin, visXmax);
      const hi = Math.min(i1, len);
      for (let i = i0; i < hi; i++) {
        const y = ys[i];
        if (Number.isFinite(y)) {
          if (y < ymin) ymin = y;
          if (y > ymax) ymax = y;
        }
      }
    }
    if (model.limitLines) {
      for (const l of model.limitLines) {
        if (Number.isFinite(l.value)) {
          if (l.value < ymin) ymin = l.value;
          if (l.value > ymax) ymax = l.value;
        }
      }
    }
    // Markers (own trades, bot trades) should also drive the y range so
    // fills outside the current quote band aren't clipped out of view.
    if (model.markers) {
      for (const mk of model.markers) {
        const xs = mk.xs;
        const ys = mk.ys;
        const len = Math.min(xs.length, ys.length);
        for (let i = 0; i < len; i++) {
          const x = xs[i];
          if (x < visXmin || x > visXmax) continue;
          const y = ys[i];
          if (Number.isFinite(y)) {
            if (y < ymin) ymin = y;
            if (y > ymax) ymax = y;
          }
        }
      }
    }
    if (!Number.isFinite(ymin)) {
      ymin = 0;
      ymax = 1;
    }
    if (ymin === ymax) {
      const pad = Math.abs(ymin) * 0.05 || 1;
      ymin -= pad;
      ymax += pad;
    } else {
      const pad = (ymax - ymin) * 0.06;
      ymin -= pad;
      ymax += pad;
    }
    model._ymin = ymin;
    model._ymax = ymax;
  }

  function niceStep(span, targetTicks) {
    const rough = span / Math.max(1, targetTicks);
    const pow = Math.pow(10, Math.floor(Math.log10(rough)));
    const n = rough / pow;
    let step;
    if (n < 1.5) step = 1;
    else if (n < 3) step = 2;
    else if (n < 7) step = 5;
    else step = 10;
    return step * pow;
  }

  function render() {
    if (!model) {
      const { w, h } = sizeCanvas();
      ctx.save();
      ctx.scale(DPR, DPR);
      ctx.clearRect(0, 0, w, h);
      ctx.restore();
      return;
    }
    const { w, h } = sizeCanvas();
    computeBounds();

    ctx.save();
    ctx.scale(DPR, DPR);
    ctx.clearRect(0, 0, w, h);

    plot = { left: 48, top: 8, width: w - 54, height: h - 28 };
    if (plot.width < 20 || plot.height < 20) {
      ctx.restore();
      return;
    }

    const xmin = visibleXMin();
    const xmax = visibleXMax();
    const { _ymin: ymin, _ymax: ymax } = model;
    const px = (x) => plot.left + ((x - xmin) / (xmax - xmin)) * plot.width;
    const py = (y) =>
      plot.top + plot.height - ((y - ymin) / (ymax - ymin)) * plot.height;

    // Grid + axes
    ctx.lineWidth = 1;
    ctx.strokeStyle = GRID_COLOR;
    ctx.fillStyle = AXIS_COLOR;
    ctx.font =
      "10px JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textBaseline = "middle";

    const yStep = niceStep(ymax - ymin, 5);
    const yStart = Math.ceil(ymin / yStep) * yStep;
    ctx.beginPath();
    for (let v = yStart; v <= ymax; v += yStep) {
      const y = Math.round(py(v)) + 0.5;
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.left + plot.width, y);
    }
    ctx.stroke();

    ctx.textAlign = "right";
    for (let v = yStart; v <= ymax; v += yStep) {
      ctx.fillText(model.yFormat(v), plot.left - 6, py(v));
    }

    const xStep = niceStep(xmax - xmin, 6);
    const xStart = Math.ceil(xmin / xStep) * xStep;
    ctx.beginPath();
    for (let v = xStart; v <= xmax; v += xStep) {
      const x = Math.round(px(v)) + 0.5;
      ctx.moveTo(x, plot.top);
      ctx.lineTo(x, plot.top + plot.height);
    }
    ctx.stroke();

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let v = xStart; v <= xmax; v += xStep) {
      ctx.fillText(model.xFormat(v), px(v), plot.top + plot.height + 4);
    }

    // Limit lines
    if (model.limitLines) {
      ctx.save();
      for (const l of model.limitLines) {
        if (!Number.isFinite(l.value)) continue;
        ctx.strokeStyle = l.color;
        ctx.lineWidth = 1;
        if (l.dash) ctx.setLineDash(l.dash);
        const y = Math.round(py(l.value)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(plot.left, y);
        ctx.lineTo(plot.left + plot.width, y);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.restore();
    }

    // Series (clipped). For each series we slice to the visible x-range and
    // run LTTB against that slice, so zooming in resamples at the new scale
    // instead of reusing a coarse pre-downsampled dataset.
    const targetPoints = model.targetPoints ?? DEFAULT_TARGET_POINTS;
    ctx.save();
    ctx.beginPath();
    ctx.rect(plot.left, plot.top, plot.width, plot.height);
    ctx.clip();
    for (const s of model.series) {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width ?? 1.2;
      if (s.dash) ctx.setLineDash(s.dash);
      ctx.beginPath();
      let penDown = false;
      const xs = s.xs;
      const ys = s.ys;
      const len = Math.min(xs.length, ys.length);
      const breakOnNaN = !!s.breakOnNaN;
      const [i0, i1] = visibleIndexRange(xs, xmin, xmax);
      const hi = Math.min(i1, len);
      const sliceLen = Math.max(0, hi - i0);
      let drawXs = xs;
      let drawYs = ys;
      let drawStart = i0;
      let drawEnd = hi;
      // LTTB only kicks in when the visible slice has more points than the
      // target; below that threshold we draw the raw slice as-is.
      if (
        Number.isFinite(targetPoints) &&
        targetPoints >= 3 &&
        sliceLen > targetPoints
      ) {
        const sxs = xs.slice(i0, hi);
        const sys = ys.slice(i0, hi);
        const r = lttb(sxs, sys, targetPoints);
        drawXs = r.xs;
        drawYs = r.ys;
        drawStart = 0;
        drawEnd = drawXs.length;
      }
      for (let i = drawStart; i < drawEnd; i++) {
        const y = drawYs[i];
        // Default: skip NaN points without lifting the pen, so a single
        // missing sample doesn't shatter the line into hundreds of short
        // segments. With breakOnNaN the pen lifts, leaving visible gaps.
        if (!Number.isFinite(y)) {
          if (breakOnNaN) penDown = false;
          continue;
        }
        const X = px(drawXs[i]);
        const Y = py(y);
        if (!penDown) {
          ctx.moveTo(X, Y);
          penDown = true;
        } else {
          ctx.lineTo(X, Y);
        }
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Markers (scatter points drawn over the line series)
    if (model.markers) {
      for (const m of model.markers) {
        const size = m.size ?? 5;
        const half = size / 2;
        const xs = m.xs;
        const ys = m.ys;
        const len = Math.min(xs.length, ys.length);
        for (let i = 0; i < len; i++) {
          const y = ys[i];
          const x = xs[i];
          if (!Number.isFinite(y) || !Number.isFinite(x)) continue;
          if (x < xmin || x > xmax) continue;
          const X = px(x);
          const Y = py(y);
          // Optional dark outline (enlarged shape first) so markers pop
          // on top of busy line series. Opt in via m.outline.
          if (m.outline) {
            ctx.fillStyle = m.outline;
            drawMarker(ctx, X, Y, m.shape ?? "dot", size + 3, half + 1.5);
          }
          ctx.fillStyle = m.color;
          drawMarker(ctx, X, Y, m.shape ?? "dot", size, half);
          // Optional white-ish ring on top for double-pop contrast.
          if (m.ring) {
            ctx.strokeStyle = m.ring;
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.arc(X, Y, half + 0.5, 0, Math.PI * 2);
            ctx.stroke();
          }
        }
      }
    }
    ctx.restore();

    // Active drag-zoom rectangle
    if (dragStart && dragStart.started) {
      const a = Math.max(
        plot.left,
        Math.min(plot.left + plot.width, dragStart.pxStart)
      );
      const b = Math.max(
        plot.left,
        Math.min(plot.left + plot.width, dragStart.pxCur)
      );
      const left = Math.min(a, b);
      const width = Math.abs(b - a);
      ctx.fillStyle = ZOOM_BG;
      ctx.fillRect(left, plot.top, width, plot.height);
      ctx.strokeStyle = ZOOM_EDGE;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(left + 0.5, plot.top);
      ctx.lineTo(left + 0.5, plot.top + plot.height);
      ctx.moveTo(left + width - 0.5, plot.top);
      ctx.lineTo(left + width - 0.5, plot.top + plot.height);
      ctx.stroke();
    }

    // Crosshair
    const chosen = hoverLogicalX ?? cursorX;
    if (chosen !== null && chosen >= xmin && chosen <= xmax) {
      ctx.save();
      ctx.strokeStyle = MUTED_TEXT + "99";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      const x = Math.round(px(chosen)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, plot.top);
      ctx.lineTo(x, plot.top + plot.height);
      ctx.stroke();
      ctx.restore();

      // Readout under crosshair
      ctx.save();
      ctx.font =
        "10px JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      const tsLabel = model.xFormat(chosen);
      const m = ctx.measureText(tsLabel);
      const bgW = m.width + 6;
      const labelX = px(chosen);
      const bgX = Math.min(
        Math.max(labelX - bgW / 2, plot.left),
        plot.left + plot.width - bgW
      );
      ctx.fillStyle = "rgba(9,9,11,0.82)";
      ctx.fillRect(bgX, plot.top + plot.height - 14, bgW, 12);
      ctx.fillStyle = MUTED_TEXT;
      ctx.fillText(tsLabel, bgX + bgW / 2, plot.top + plot.height - 2);
      ctx.restore();
    }

    // Zoom indicator in the top-left of the plot area
    if (xView) {
      ctx.save();
      ctx.font =
        "9px JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillStyle = ZOOM_EDGE;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText("zoom · right-click to reset", plot.left + 4, plot.top + 2);
      ctx.restore();
    }

    ctx.restore();

    if (onHover || model.onRender) {
      const values = model.series.map((s) =>
        chosen === null ? null : sampleSeries(s, chosen)
      );
      if (onHover) onHover(values, chosen);
      if (model.onRender) model.onRender(values, chosen);
    }
  }

  function sampleSeries(s, x) {
    const xs = s.xs;
    if (!xs.length) return null;
    let lo = 0;
    let hi = xs.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (xs[mid] < x) lo = mid + 1;
      else hi = mid;
    }
    const y = s.ys[lo];
    return Number.isFinite(y) ? y : null;
  }

  function setData(m) {
    // Cache x bounds once; they don't change between renders and the raw
    // series arrays can be large (hundreds of thousands of points when the
    // caller lets the chart do its own viewport-aware downsampling).
    let xmin = Infinity;
    let xmax = -Infinity;
    for (const s of m.series) {
      const xs = s.xs;
      for (let i = 0; i < xs.length; i++) {
        const x = xs[i];
        if (Number.isFinite(x)) {
          if (x < xmin) xmin = x;
          if (x > xmax) xmax = x;
        }
      }
    }
    if (!Number.isFinite(xmin)) {
      xmin = 0;
      xmax = 1;
    }
    if (xmin === xmax) xmax = xmin + 1;
    m._xmin = xmin;
    m._xmax = xmax;
    model = m;
    render();
  }

  function setCursorX(x) {
    cursorX = x;
    render();
  }

  function setXView(min, max) {
    xView = [min, max];
    render();
  }

  function resetXView() {
    xView = null;
    render();
  }

  function destroy() {
    ro.disconnect();
    if (resizeRaf != null) cancelAnimationFrame(resizeRaf);
  }

  return { setData, setCursorX, setXView, resetXView, render, destroy };
}

function drawMarker(ctx, X, Y, shape, size, half) {
  if (shape === "up") {
    ctx.beginPath();
    ctx.moveTo(X, Y - half);
    ctx.lineTo(X + half, Y + half);
    ctx.lineTo(X - half, Y + half);
    ctx.closePath();
    ctx.fill();
  } else if (shape === "down") {
    ctx.beginPath();
    ctx.moveTo(X, Y + half);
    ctx.lineTo(X + half, Y - half);
    ctx.lineTo(X - half, Y - half);
    ctx.closePath();
    ctx.fill();
  } else if (shape === "diamond") {
    ctx.beginPath();
    ctx.moveTo(X, Y - half);
    ctx.lineTo(X + half, Y);
    ctx.lineTo(X, Y + half);
    ctx.lineTo(X - half, Y);
    ctx.closePath();
    ctx.fill();
  } else {
    // dot (default)
    ctx.beginPath();
    ctx.arc(X, Y, half, 0, Math.PI * 2);
    ctx.fill();
  }
  void size;
}

