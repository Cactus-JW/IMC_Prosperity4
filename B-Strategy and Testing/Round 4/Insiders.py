"""
Insiders.py  —  Round 4 named-trader analysis dashboard.
Run:  python "B-Strategy and Testing/Round 4/Insiders.py"
Opens at  http://127.0.0.1:8050
"""

import os
import pandas as pd
import numpy as np
import networkx as nx
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from dash import Dash, dcc, html, Input, Output

# ── Paths ──────────────────────────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "A-Historical Data", "Round 4"))
DAYS = [1, 2, 3]

# ── Data loading ───────────────────────────────────────────────────────────────

def load_trades() -> pd.DataFrame:
    dfs = []
    for d in DAYS:
        df = pd.read_csv(os.path.join(DATA_DIR, f"trades_round_4_day_{d}.csv"), sep=";")
        df["day"] = d
        dfs.append(df)
    t = pd.concat(dfs, ignore_index=True)
    t["abs_ts"] = (t["day"] - 1) * 1_000_000 + t["timestamp"]
    t["buyer"]  = t["buyer"].fillna("").str.strip()
    t["seller"] = t["seller"].fillna("").str.strip()
    return t.sort_values("abs_ts").reset_index(drop=True)


def load_prices() -> pd.DataFrame:
    dfs = []
    for d in DAYS:
        df = pd.read_csv(os.path.join(DATA_DIR, f"prices_round_4_day_{d}.csv"), sep=";")
        dfs.append(df)
    p = pd.concat(dfs, ignore_index=True)
    p["abs_ts"] = (p["day"] - 1) * 1_000_000 + p["timestamp"]
    return (
        p[["abs_ts", "day", "timestamp", "product", "mid_price"]]
        .sort_values(["product", "abs_ts"])
        .reset_index(drop=True)
    )

# ── PnL (mark-to-market) ───────────────────────────────────────────────────────

def compute_pnl(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    Returns tidy DataFrame: trader, symbol, abs_ts, mid_price, position, cash, pnl.
    pnl = cash + position * mid_price  (mark-to-market at every price tick).
    """
    buys = (
        trades[trades["buyer"] != ""][["abs_ts", "buyer", "symbol", "price", "quantity"]]
        .rename(columns={"buyer": "trader"}).copy()
    )
    buys["pos_delta"]  =  buys["quantity"]
    buys["cash_delta"] = -buys["price"] * buys["quantity"]

    sells = (
        trades[trades["seller"] != ""][["abs_ts", "seller", "symbol", "price", "quantity"]]
        .rename(columns={"seller": "trader"}).copy()
    )
    sells["pos_delta"]  = -sells["quantity"]
    sells["cash_delta"] =  sells["price"] * sells["quantity"]

    events = (
        pd.concat([
            buys[["abs_ts", "trader", "symbol", "pos_delta", "cash_delta"]],
            sells[["abs_ts", "trader", "symbol", "pos_delta", "cash_delta"]],
        ])
        .groupby(["trader", "symbol", "abs_ts"])[["pos_delta", "cash_delta"]].sum()
        .reset_index()
    )

    result_dfs = []
    for (trader, symbol), ev in events.groupby(["trader", "symbol"]):
        ev_idx = ev.set_index("abs_ts")[["pos_delta", "cash_delta"]]
        sym_p  = prices[prices["product"] == symbol][["abs_ts", "mid_price"]].set_index("abs_ts")

        combined = sym_p.join(ev_idx, how="left")
        combined[["pos_delta", "cash_delta"]] = combined[["pos_delta", "cash_delta"]].fillna(0.0)
        combined["position"] = combined["pos_delta"].cumsum()
        combined["cash"]     = combined["cash_delta"].cumsum()
        combined["pnl"]      = combined["cash"] + combined["position"] * combined["mid_price"]
        combined["trader"]   = trader
        combined["symbol"]   = symbol
        result_dfs.append(
            combined.reset_index()[["abs_ts", "trader", "symbol", "mid_price", "position", "cash", "pnl"]]
        )

    return pd.concat(result_dfs, ignore_index=True)


def compute_sharpe(pnl_df: pd.DataFrame) -> pd.DataFrame:
    total = (
        pnl_df.groupby(["trader", "abs_ts"])["pnl"].sum()
        .reset_index().sort_values(["trader", "abs_ts"])
    )
    rows = []
    for trader, grp in total.groupby("trader"):
        diffs = grp["pnl"].diff().dropna()
        sharpe = float(diffs.mean() / diffs.std()) if len(diffs) >= 2 and diffs.std() > 0 else 0.0
        rows.append({"trader": trader, "sharpe": sharpe})
    return pd.DataFrame(rows).sort_values("sharpe", ascending=True)

# ── Info leakage ───────────────────────────────────────────────────────────────

def compute_info_leakage(trades: pd.DataFrame, prices: pd.DataFrame, forward_ticks: int = 10) -> pd.DataFrame:
    """
    Per-trader, per-symbol average excess return over the next `forward_ticks`
    price steps (100 units each) in the direction of their trade.
    Positive = trades ahead of subsequent price moves.
    """
    buys  = trades[trades["buyer"]  != ""][["abs_ts", "buyer",  "symbol"]].rename(columns={"buyer":  "trader"}).assign(direction= 1)
    sells = trades[trades["seller"] != ""][["abs_ts", "seller", "symbol"]].rename(columns={"seller": "trader"}).assign(direction=-1)
    events = pd.concat([buys, sells]).reset_index(drop=True)
    events["event_id"] = events.index

    results = []
    for symbol, sym_ev in events.groupby("symbol"):
        sym_prices = (
            prices[prices["product"] == symbol][["abs_ts", "mid_price"]]
            .sort_values("abs_ts").reset_index(drop=True)
        )
        if sym_prices.empty:
            continue

        ev_sorted = sym_ev.sort_values("abs_ts").copy()

        m0 = pd.merge_asof(ev_sorted, sym_prices, on="abs_ts", direction="nearest").rename(columns={"mid_price": "mid_0"})

        fwd_query = pd.DataFrame({"event_id": m0["event_id"], "abs_ts": m0["abs_ts"] + forward_ticks * 100})
        fwd_merge = pd.merge_asof(fwd_query.sort_values("abs_ts"), sym_prices, on="abs_ts", direction="nearest").rename(columns={"mid_price": "mid_fwd"})

        m0 = m0.merge(fwd_merge[["event_id", "mid_fwd"]], on="event_id", how="left")
        m0["excess"] = np.where(
            m0["mid_0"] != 0,
            m0["direction"] * (m0["mid_fwd"] - m0["mid_0"]) / m0["mid_0"],
            np.nan,
        )
        results.append(m0[["trader", "symbol", "excess"]])

    if not results:
        return pd.DataFrame(columns=["trader", "symbol", "excess_return"])
    df = pd.concat(results)
    return df.groupby(["trader", "symbol"])["excess"].mean().reset_index().rename(columns={"excess": "excess_return"})

# ── Options flow ───────────────────────────────────────────────────────────────

def compute_options_flow(trades: pd.DataFrame) -> pd.DataFrame:
    vev = trades[trades["symbol"].str.startswith("VEV_")].copy()
    vev["strike"] = vev["symbol"].str[4:].astype(int)

    buys  = vev[vev["buyer"]  != ""][["buyer",  "strike", "quantity"]].rename(columns={"buyer":  "trader"})
    buys["net_qty"]  =  buys["quantity"]
    sells = vev[vev["seller"] != ""][["seller", "strike", "quantity"]].rename(columns={"seller": "trader"})
    sells["net_qty"] = -sells["quantity"]

    combined = pd.concat([buys[["trader", "strike", "net_qty"]], sells[["trader", "strike", "net_qty"]]])
    return combined.groupby(["trader", "strike"])["net_qty"].sum().reset_index()

# ── Network figure ─────────────────────────────────────────────────────────────

def build_network_fig(trades: pd.DataFrame, symbol_filter: str, tcolors: dict) -> go.Figure:
    t = trades if symbol_filter == "All" else trades[trades["symbol"] == symbol_filter]
    named = t[(t["buyer"] != "") & (t["seller"] != "")]
    if named.empty:
        fig = go.Figure()
        fig.add_annotation(text="No named counterparty trades for this asset", showarrow=False, font=dict(size=16))
        return fig

    edge_vol = named.groupby(["buyer", "seller"])["quantity"].sum().reset_index()
    G = nx.Graph()
    for _, row in edge_vol.iterrows():
        b, s, w = row["buyer"], row["seller"], int(row["quantity"])
        if G.has_edge(b, s):
            G[b][s]["weight"] += w
        else:
            G.add_edge(b, s, weight=w)

    pos = nx.spring_layout(G, seed=42)
    traces = []

    for u, v, data in G.edges(data=True):
        x0, y0 = pos[u]; x1, y1 = pos[v]
        traces.append(go.Scatter(
            x=[x0, x1, None], y=[y0, y1, None], mode="lines",
            line=dict(width=max(1.0, data["weight"] / 200), color="#bbb"),
            hoverinfo="none", showlegend=False,
        ))

    for node in G.nodes():
        x, y = pos[node]
        vol = sum(d["weight"] for _, _, d in G.edges(node, data=True))
        traces.append(go.Scatter(
            x=[x], y=[y], mode="markers+text", name=node,
            text=[node], textposition="top center",
            hovertext=[f"<b>{node}</b><br>Total volume traded: {vol}"],
            hoverinfo="text",
            marker=dict(
                size=max(16, 10 + vol / 200),
                color=tcolors.get(node, "#636EFA"),
                line=dict(width=2, color="white"),
            ),
        ))

    fig = go.Figure(traces)
    fig.update_layout(
        title=f"Counterparty network — {symbol_filter}",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor="white", paper_bgcolor="#fafafa",
        legend=dict(title="Trader"),
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig

# ── Precompute ─────────────────────────────────────────────────────────────────

print("Loading data...")
trades_df = load_trades()
prices_df = load_prices()
print(f"  {len(trades_df)} trades, {len(prices_df)} price rows")

print("Computing PnL...")
pnl_df = compute_pnl(trades_df, prices_df)
sharpe_df = compute_sharpe(pnl_df)
leakage_df = compute_info_leakage(trades_df, prices_df, forward_ticks=10)
options_df = compute_options_flow(trades_df)
print("  Done.")

ALL_TRADERS = sorted(pnl_df["trader"].unique())
ALL_SYMBOLS = sorted(pnl_df["symbol"].unique())

_PALETTE = px.colors.qualitative.Plotly
TCOLORS  = {t: _PALETTE[i % len(_PALETTE)] for i, t in enumerate(ALL_TRADERS)}

# Day boundary vertical lines for time-series charts
_DAY_LINES = [
    dict(type="line", x0=1_000_000, x1=1_000_000, y0=0, y1=1, yref="paper",
         line=dict(color="gray", dash="dot", width=1)),
    dict(type="line", x0=2_000_000, x1=2_000_000, y0=0, y1=1, yref="paper",
         line=dict(color="gray", dash="dot", width=1)),
]
_DAY_ANNOTATIONS = [
    dict(x=500_000,   y=1.02, xref="x", yref="paper", text="Day 1", showarrow=False, font=dict(size=11, color="gray")),
    dict(x=1_500_000, y=1.02, xref="x", yref="paper", text="Day 2", showarrow=False, font=dict(size=11, color="gray")),
    dict(x=2_500_000, y=1.02, xref="x", yref="paper", text="Day 3", showarrow=False, font=dict(size=11, color="gray")),
]

# ── App layout ─────────────────────────────────────────────────────────────────

app = Dash(__name__)
app.title = "Insiders — Round 4"

_show = {"display": "inline-block", "marginRight": "20px", "verticalAlign": "top"}
_hide = {"display": "none"}

def _label(text):
    return html.Label(text, style={"fontSize": "12px", "color": "#555", "marginBottom": "4px", "display": "block"})

trader_opts  = [{"label": t, "value": t} for t in ALL_TRADERS]
symbol_opts  = [{"label": s, "value": s} for s in ALL_SYMBOLS]

app.layout = html.Div([
    html.H2("Round 4 — Insider Trader Analysis",
            style={"textAlign": "center", "margin": "16px 0", "color": "#222"}),
    dcc.Tabs(id="tabs", value="pnl", children=[
        dcc.Tab(label="PnL History",    value="pnl"),
        dcc.Tab(label="Positions",      value="pos"),
        dcc.Tab(label="Sharpe",         value="sharpe"),
        dcc.Tab(label="Info Leakage",   value="leakage"),
        dcc.Tab(label="Timing Heatmap", value="heatmap"),
        dcc.Tab(label="Network",        value="network"),
        dcc.Tab(label="Options Flow",   value="options"),
        dcc.Tab(label="Price & Trades", value="price_trades"),
    ]),
    html.Div([
        # PnL / Positions
        html.Div([_label("Traders"),
                  dcc.Dropdown(id="sel-traders", options=trader_opts, value=ALL_TRADERS,
                               multi=True, clearable=False, style={"minWidth": "320px"})],
                 id="wrap-traders", style=_show),
        html.Div([_label("Asset"),
                  dcc.Dropdown(id="sel-symbol",
                               options=[{"label": "All (sum)", "value": "ALL"}] + symbol_opts,
                               value="ALL", clearable=False, style={"minWidth": "200px"})],
                 id="wrap-symbol", style=_show),
        # Heatmap
        html.Div([_label("Asset"),
                  dcc.Dropdown(id="sel-heatmap-symbol",
                               options=[{"label": "All", "value": "ALL"}] + symbol_opts,
                               value="ALL", clearable=False, style={"minWidth": "200px"})],
                 id="wrap-heatmap-sym", style=_hide),
        html.Div([_label("Trader"),
                  dcc.Dropdown(id="sel-heatmap-traders", options=trader_opts,
                               value=ALL_TRADERS[0], clearable=False, style={"minWidth": "200px"})],
                 id="wrap-heatmap-traders", style=_hide),
        # Network
        html.Div([_label("Asset"),
                  dcc.Dropdown(id="sel-net-symbol",
                               options=[{"label": "All", "value": "All"}] + symbol_opts,
                               value="All", clearable=False, style={"minWidth": "200px"})],
                 id="wrap-net-sym", style=_hide),
        # Options
        html.Div([_label("Traders"),
                  dcc.Dropdown(id="sel-opt-traders", options=trader_opts, value=ALL_TRADERS,
                               multi=True, clearable=False, style={"minWidth": "320px"})],
                 id="wrap-opt-traders", style=_hide),
        # Leakage slider
        html.Div([_label("Forward ticks  N"),
                  html.Div(dcc.Slider(id="sel-fwd-ticks", min=1, max=50, step=1, value=10,
                               marks={i: str(i) for i in [1, 5, 10, 20, 30, 50]},
                               tooltip={"placement": "bottom", "always_visible": True}),
                           style={"width": "420px"})],
                 id="wrap-fwd-ticks", style=_hide),
        # Price & Trades
        html.Div([_label("Asset"),
                  dcc.Dropdown(id="sel-pt-symbol", options=symbol_opts,
                               value=ALL_SYMBOLS[0], clearable=False, style={"minWidth": "200px"})],
                 id="wrap-pt-symbol", style=_hide),
        html.Div([_label("Trader"),
                  dcc.Dropdown(id="sel-pt-trader", options=trader_opts,
                               value=ALL_TRADERS[0], clearable=False, style={"minWidth": "200px"})],
                 id="wrap-pt-trader", style=_hide),
    ], style={"padding": "12px 24px", "background": "#f5f5f5",
              "borderBottom": "1px solid #ddd", "display": "flex", "flexWrap": "wrap",
              "alignItems": "flex-start", "gap": "8px"}),
    dcc.Graph(id="main-graph", style={"height": "640px"}),
], style={"fontFamily": "Arial, sans-serif", "maxWidth": "1500px", "margin": "0 auto"})

# ── Control visibility ─────────────────────────────────────────────────────────

@app.callback(
    Output("wrap-traders",        "style"),
    Output("wrap-symbol",         "style"),
    Output("wrap-heatmap-sym",    "style"),
    Output("wrap-heatmap-traders","style"),
    Output("wrap-net-sym",        "style"),
    Output("wrap-opt-traders",    "style"),
    Output("wrap-fwd-ticks",      "style"),
    Output("wrap-pt-symbol",      "style"),
    Output("wrap-pt-trader",      "style"),
    Input("tabs", "value"),
)
def toggle_controls(tab):
    s, h = dict(_show), dict(_hide)
    vis = {
        "pnl":          [s, s, h, h, h, h, h, h, h],
        "pos":          [s, s, h, h, h, h, h, h, h],
        "sharpe":       [h, h, h, h, h, h, h, h, h],
        "leakage":      [h, h, h, h, h, h, s, h, h],
        "heatmap":      [h, h, s, s, h, h, h, h, h],
        "network":      [h, h, h, h, s, h, h, h, h],
        "options":      [h, h, h, h, h, s, h, h, h],
        "price_trades": [h, h, h, h, h, h, h, s, s],
    }
    return tuple(vis.get(tab, [h] * 9))

# ── Main graph ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("main-graph", "figure"),
    Input("tabs",                 "value"),
    Input("sel-traders",          "value"),
    Input("sel-symbol",           "value"),
    Input("sel-fwd-ticks",        "value"),
    Input("sel-heatmap-symbol",   "value"),
    Input("sel-heatmap-traders",  "value"),
    Input("sel-net-symbol",       "value"),
    Input("sel-opt-traders",      "value"),
    Input("sel-pt-symbol",        "value"),
    Input("sel-pt-trader",        "value"),
)
def update_graph(tab, traders, symbol, fwd_ticks, heatmap_sym, heatmap_traders, net_sym, opt_traders, pt_symbol, pt_trader):
    traders         = traders         or ALL_TRADERS
    opt_traders     = opt_traders     or ALL_TRADERS
    heatmap_traders = heatmap_traders or ALL_TRADERS[0]

    # ── PnL History ────────────────────────────────────────────────────────────
    if tab == "pnl":
        if symbol == "ALL":
            series = (
                pnl_df[pnl_df["trader"].isin(traders)]
                .groupby(["trader", "abs_ts"])["pnl"].sum()
                .reset_index()
            )
            ytitle = "Total PnL (all assets)"
        else:
            series = pnl_df[(pnl_df["trader"].isin(traders)) & (pnl_df["symbol"] == symbol)][["trader", "abs_ts", "pnl"]]
            ytitle = f"PnL — {symbol}"

        fig = go.Figure()
        for trader, grp in series.groupby("trader"):
            grp = grp.sort_values("abs_ts")
            fig.add_trace(go.Scatter(
                x=grp["abs_ts"], y=grp["pnl"],
                mode="lines", name=trader,
                line=dict(color=TCOLORS.get(trader)),
                hovertemplate=f"<b>{trader}</b><br>ts=%{{x}}<br>PnL=%{{y:.1f}}<extra></extra>",
            ))
        fig.update_layout(
            title=f"Cumulative PnL — {symbol}",
            xaxis_title="Absolute timestamp", yaxis_title=ytitle,
            shapes=_DAY_LINES, annotations=_DAY_ANNOTATIONS,
            legend=dict(title="Trader"), hovermode="x unified",
            plot_bgcolor="white", paper_bgcolor="#fafafa",
        )
        fig.add_hline(y=0, line_dash="dot", line_color="black", line_width=0.8)
        return fig

    # ── Positions ──────────────────────────────────────────────────────────────
    elif tab == "pos":
        if symbol == "ALL":
            # Show positions for VELVETFRUIT_EXTRACT by default when all selected
            sym_show = "VELVETFRUIT_EXTRACT"
            subtitle  = "(showing VELVETFRUIT_EXTRACT — select a specific asset to change)"
        else:
            sym_show = symbol
            subtitle  = ""
        series = pnl_df[(pnl_df["trader"].isin(traders)) & (pnl_df["symbol"] == sym_show)]

        fig = go.Figure()
        for trader, grp in series.groupby("trader"):
            grp = grp.sort_values("abs_ts")
            fig.add_trace(go.Scatter(
                x=grp["abs_ts"], y=grp["position"],
                mode="lines", name=trader,
                line=dict(color=TCOLORS.get(trader)),
                hovertemplate=f"<b>{trader}</b><br>ts=%{{x}}<br>pos=%{{y}}<extra></extra>",
            ))
        fig.update_layout(
            title=f"Net Position — {sym_show} {subtitle}",
            xaxis_title="Absolute timestamp", yaxis_title="Net position (units)",
            shapes=_DAY_LINES, annotations=_DAY_ANNOTATIONS,
            legend=dict(title="Trader"), hovermode="x unified",
            plot_bgcolor="white", paper_bgcolor="#fafafa",
        )
        fig.add_hline(y=0, line_dash="dot", line_color="black", line_width=0.8)
        return fig

    # ── Sharpe Summary ─────────────────────────────────────────────────────────
    elif tab == "sharpe":
        fig = go.Figure(go.Bar(
            x=sharpe_df["sharpe"],
            y=sharpe_df["trader"],
            orientation="h",
            marker_color=[TCOLORS.get(t, "#636EFA") for t in sharpe_df["trader"]],
            hovertemplate="<b>%{y}</b><br>Sharpe = %{x:.4f}<extra></extra>",
        ))
        fig.add_vline(x=0, line_dash="dot", line_color="black", line_width=1)
        fig.update_layout(
            title="Per-trader Sharpe ratio (tick-by-tick total PnL)",
            xaxis_title="Sharpe ratio", yaxis_title="",
            plot_bgcolor="white", paper_bgcolor="#fafafa",
            margin=dict(l=120),
        )
        return fig

    # ── Info Leakage ───────────────────────────────────────────────────────────
    elif tab == "leakage":
        ldf = compute_info_leakage(trades_df, prices_df, forward_ticks=fwd_ticks or 10)
        if ldf.empty:
            return go.Figure().add_annotation(text="No data", showarrow=False)

        pivot = ldf.pivot(index="symbol", columns="trader", values="excess_return").fillna(0.0)
        fig = go.Figure()
        for trader in pivot.columns:
            fig.add_trace(go.Bar(
                name=trader,
                x=pivot.index.tolist(),
                y=pivot[trader].tolist(),
                marker_color=TCOLORS.get(trader),
                hovertemplate=f"<b>{trader}</b><br>%{{x}}<br>avg excess return = %{{y:.6f}}<extra></extra>",
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="black", line_width=0.8)
        fig.update_layout(
            title=f"Info leakage — avg excess return {fwd_ticks} ticks after trade (positive = front-running)",
            xaxis_title="Asset", yaxis_title="Avg directional excess return",
            barmode="group",
            legend=dict(title="Trader"),
            plot_bgcolor="white", paper_bgcolor="#fafafa",
        )
        return fig

    # ── Timing Heatmap ─────────────────────────────────────────────────────────
    elif tab == "heatmap":
        t = trades_df if heatmap_sym == "ALL" else trades_df[trades_df["symbol"] == heatmap_sym]
        buys  = t[t["buyer"]  != ""][["day", "timestamp", "buyer",  "quantity"]].rename(columns={"buyer":  "trader"})
        sells = t[t["seller"] != ""][["day", "timestamp", "seller", "quantity"]].rename(columns={"seller": "trader"})
        events = pd.concat([buys, sells]).reset_index(drop=True)

        if events.empty:
            return go.Figure().add_annotation(text="No data", showarrow=False)

        n_bins   = 60
        bin_size = 1_000_000 / n_bins
        events["time_bin"] = (events["timestamp"] / bin_size).astype(int).clip(0, n_bins - 1)

        bin_centres = [(i + 0.5) * bin_size for i in range(n_bins)]
        bin_labels  = [f"{int(c / 1000)}k" for c in bin_centres]

        events = events[events["trader"] == heatmap_traders]
        if events.empty:
            return go.Figure().add_annotation(text="No trades for this trader / asset combination", showarrow=False)

        all_counts = events.groupby(["day", "time_bin"]).size()
        zmax = int(all_counts.max()) if not all_counts.empty else 1

        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            subplot_titles=["Day 1", "Day 2", "Day 3"],
            vertical_spacing=0.10,
        )

        for row, day in enumerate(DAYS, start=1):
            counts = (
                events[events["day"] == day]
                .groupby("time_bin").size()
                .reindex(range(n_bins), fill_value=0)
            )
            fig.add_trace(go.Heatmap(
                z=[counts.values],
                x=[bin_labels[i] for i in range(n_bins)],
                y=[heatmap_traders],
                colorscale="Blues",
                zmin=0, zmax=zmax,
                showscale=(row == 1),
                hoverongaps=False,
                hovertemplate=f"Day {day}<br>Time: %{{x}}<br>Trades: %{{z}}<extra></extra>",
            ), row=row, col=1)

        fig.update_xaxes(tickangle=-45, row=3, col=1)
        fig.update_layout(
            title=f"Trade timing — {heatmap_traders}  |  {heatmap_sym}",
            plot_bgcolor="white", paper_bgcolor="#fafafa",
            margin=dict(b=80, t=60),
            height=560,
        )
        return fig

    # ── Network ────────────────────────────────────────────────────────────────
    elif tab == "network":
        return build_network_fig(trades_df, net_sym or "All", TCOLORS)

    # ── Options Flow ───────────────────────────────────────────────────────────
    elif tab == "options":
        odf = options_df[options_df["trader"].isin(opt_traders)]
        if odf.empty:
            return go.Figure().add_annotation(text="No options data", showarrow=False)

        all_strikes = sorted(odf["strike"].unique())
        fig = go.Figure()
        for trader, grp in odf.groupby("trader"):
            strike_map = grp.set_index("strike")["net_qty"].to_dict()
            ys = [strike_map.get(s, 0) for s in all_strikes]
            fig.add_trace(go.Bar(
                name=trader,
                x=[str(s) for s in all_strikes],
                y=ys,
                marker_color=TCOLORS.get(trader),
                hovertemplate=f"<b>{trader}</b><br>Strike %{{x}}<br>Net position: %{{y}}<extra></extra>",
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="black", line_width=0.8)
        fig.update_layout(
            title="Options flow — net position per trader per strike (VEV_* vouchers)",
            xaxis_title="Strike price", yaxis_title="Net position (+ = net long)",
            barmode="group",
            legend=dict(title="Trader"),
            plot_bgcolor="white", paper_bgcolor="#fafafa",
        )
        return fig

    # ── Price & Trades ─────────────────────────────────────────────────────────
    elif tab == "price_trades":
        sym    = pt_symbol or ALL_SYMBOLS[0]
        trader = pt_trader or ALL_TRADERS[0]

        price_series = prices_df[prices_df["product"] == sym].sort_values("abs_ts")

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=price_series["abs_ts"], y=price_series["mid_price"],
            mode="lines", name="Mid Price",
            line=dict(color="#aaa", width=1),
            hovertemplate="ts=%{x}<br>mid=%{y:.1f}<extra></extra>",
        ))

        trader_buys = trades_df[(trades_df["buyer"] == trader) & (trades_df["symbol"] == sym)]
        if not trader_buys.empty:
            fig.add_trace(go.Scatter(
                x=trader_buys["abs_ts"], y=trader_buys["price"],
                mode="markers", name="Buy",
                marker=dict(symbol="triangle-up", color="green", size=11, line=dict(width=1, color="darkgreen")),
                customdata=trader_buys["quantity"].values,
                hovertemplate=f"<b>{trader} BUY</b><br>ts=%{{x}}<br>price=%{{y}}<br>qty=%{{customdata}}<extra></extra>",
            ))

        trader_sells = trades_df[(trades_df["seller"] == trader) & (trades_df["symbol"] == sym)]
        if not trader_sells.empty:
            fig.add_trace(go.Scatter(
                x=trader_sells["abs_ts"], y=trader_sells["price"],
                mode="markers", name="Sell",
                marker=dict(symbol="triangle-down", color="crimson", size=11, line=dict(width=1, color="darkred")),
                customdata=trader_sells["quantity"].values,
                hovertemplate=f"<b>{trader} SELL</b><br>ts=%{{x}}<br>price=%{{y}}<br>qty=%{{customdata}}<extra></extra>",
            ))

        fig.update_layout(
            title=f"Price history — {sym}  |  {trader} trades",
            xaxis_title="Absolute timestamp", yaxis_title="Price",
            shapes=_DAY_LINES, annotations=_DAY_ANNOTATIONS,
            legend=dict(title=""),
            plot_bgcolor="white", paper_bgcolor="#fafafa",
            hovermode="closest",
        )
        return fig

    return go.Figure()

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
