import {
  subscribe,
  getState,
  getReference,
  setFillsShowAll,
  setFillsCurrentOnly,
} from "../store.js";

export function mountOwnFills({ bodyEl, titleEl, showAllInput, currentOnlyInput }) {
  showAllInput.addEventListener("change", (e) =>
    setFillsShowAll(e.target.checked)
  );
  currentOnlyInput.addEventListener("change", (e) =>
    setFillsCurrentOnly(e.target.checked)
  );

  function render() {
    const state = getState();
    const ref = getReference(state);
    showAllInput.checked = state.fillsShowAll;
    currentOnlyInput.checked = state.fillsCurrentOnly;
    const scopeLabel = state.fillsCurrentOnly
      ? "· current"
      : state.fillsShowAll
        ? "· all"
        : "· ±500 ts";
    titleEl.textContent = `Own Fills ${scopeLabel}`;

    if (!ref) {
      bodyEl.innerHTML = `<div class="muted" style="padding:12px;text-align:center">No strategy loaded.</div>`;
      return;
    }
    const tickIdx = state.tickIdx;
    const rawTs = ref.rawTimestamps[tickIdx] ?? 0;
    const botFills = (ref.trades ?? [])
      .filter((t) => t.buyer !== "SUBMISSION" && t.seller !== "SUBMISSION")
      .map((t) => ({
        timestamp: t.timestamp,
        product: t.symbol,
        side: "bot",
        price: t.price,
        quantity: t.quantity,
        cashFlow: 0,
      }));
    let fills = ref.ownFills.concat(botFills);
    if (state.selectedProduct)
      fills = fills.filter((f) => f.product === state.selectedProduct);
    if (state.fillsCurrentOnly) {
      fills = fills.filter((f) => f.timestamp === rawTs);
    } else if (!state.fillsShowAll) {
      const lo = rawTs - 500;
      const hi = rawTs + 500;
      fills = fills.filter((f) => f.timestamp >= lo && f.timestamp <= hi);
    }
    fills.sort((a, b) => a.timestamp - b.timestamp);

    const rows =
      fills.length === 0
        ? `<tr><td class="empty" colspan="6">No fills in this window.</td></tr>`
        : fills
            .map((f) => {
              const isBot = f.side === "bot";
              const cashCell = isBot
                ? `<td class="num muted">—</td>`
                : `<td class="num ${f.cashFlow >= 0 ? "positive" : "negative"}">${f.cashFlow >= 0 ? "+" : ""}${f.cashFlow.toFixed(0)}</td>`;
              return `
      <tr class="fills-row-${f.side}">
        <td class="left num muted">${f.timestamp}</td>
        <td class="left side">${f.side.toUpperCase()}</td>
        <td class="left">${f.product}</td>
        <td class="num">${f.price.toFixed(1)}</td>
        <td class="num">${f.quantity}</td>
        ${cashCell}
      </tr>
    `;
            })
            .join("");

    bodyEl.innerHTML = `
      <table class="data">
        <thead>
          <tr>
            <th class="left">TS</th>
            <th class="left">Side</th>
            <th class="left">Product</th>
            <th>Price</th>
            <th>Qty</th>
            <th>Cash</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  subscribe(render);
  render();
}
