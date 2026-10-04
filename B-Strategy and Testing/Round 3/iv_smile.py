"""
Implied volatility smile for VELVETFRUIT_EXTRACT options (VEV_XXXX calls).

Black-Scholes inversion with r=0 (competition has no risk-free rate).
Time convention: T = remaining_ticks / 10000  (so T=3 at day 0 t=0, T≈0 at day 2 t=999900).
Log-moneyness: m = ln(K/S) / sqrt(T).
"""

import os
import glob
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import norm
from scipy.optimize import brentq

DATA_DIR = os.path.join(os.path.dirname(__file__), "../../A-Historical Data/Round 3")
TOTAL_DAYS = 3          # round 3 has 3 trading days (0,1,2)
TICKS_PER_DAY = 10_000  # timestamps 0..999900 in steps of 100

# ── Black-Scholes call price (r=0) ─────────────────────────────────────────────
def bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (np.log(S / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * norm.cdf(d1) - K * norm.cdf(d2)

# ── Implied vol via Brent root-find ────────────────────────────────────────────
def implied_vol(price, S, K, T, tol=1e-6, low=1e-4, high=30.0):
    intrinsic = max(S - K, 0.0)
    if price <= intrinsic + tol:          # no vol content
        return np.nan
    if price >= S:                         # above max call price
        return np.nan
    try:
        f = lambda v: bs_call(S, K, T, v) - price
        if f(low) * f(high) > 0:
            return np.nan
        return brentq(f, low, high, xtol=tol)
    except Exception:
        return np.nan

# ── Load all three days ─────────────────────────────────────────────────────────
frames = []
for path in sorted(glob.glob(os.path.join(DATA_DIR, "prices_round_3_day_*.csv"))):
    df = pd.read_csv(path, sep=";")
    frames.append(df)
data = pd.concat(frames, ignore_index=True)

# Extract VEV options and the underlying
options = data[data["product"].str.startswith("VEV_")].copy()
underlying = data[data["product"] == "VELVETFRUIT_EXTRACT"][["day", "timestamp", "mid_price"]].rename(
    columns={"mid_price": "S"}
)

# Parse strike from product name
options["K"] = options["product"].str.split("_").str[1].astype(float)
options["mid_price"] = (options["bid_price_1"] + options["ask_price_1"]) / 2

# Merge underlying price
options = options.merge(underlying, on=["day", "timestamp"], how="left")

# Compute time to expiry (in units of "days")
options["elapsed_ticks"] = options["day"] * TICKS_PER_DAY + options["timestamp"] / 100
options["T"] = (TOTAL_DAYS * TICKS_PER_DAY - options["elapsed_ticks"]) / TICKS_PER_DAY

# ── Downsample to 1000-tick bins to speed up IV inversion ─────────────────────
# Each day has 10000 ticks; 1000-tick bins → 10 bins/day, manageable
options["bin"] = (options["elapsed_ticks"] // 1000).astype(int)

# Aggregate to one row per (day, bin, K): median price and underlying
agg = (options.groupby(["day", "bin", "K"])
       .agg(price=("mid_price", "median"),
            S=("S", "median"),
            T=("T", "median"),
            elapsed_ticks=("elapsed_ticks", "median"))
       .reset_index())

# Compute implied vol on the aggregated (smaller) table
def row_iv(r):
    if pd.isna(r["S"]) or r["T"] <= 0 or pd.isna(r["price"]):
        return np.nan
    return implied_vol(r["price"], r["S"], r["K"], r["T"])

print(f"Computing implied vols on {len(agg)} aggregated rows …")
agg["iv"] = agg.apply(row_iv, axis=1)
options = agg  # replace options with aggregated frame for downstream use

# Drop rows with no IV
opts_clean = options.dropna(subset=["iv", "S", "T"])
opts_clean = opts_clean[opts_clean["T"] > 0.01]    # avoid last few ticks

# Log-moneyness
opts_clean = opts_clean.copy()
opts_clean["m"] = np.log(opts_clean["K"] / opts_clean["S"]) / np.sqrt(opts_clean["T"])

# ── Summary per (day, K) ───────────────────────────────────────────────────────
summary = (opts_clean.groupby(["day", "K"])
           .agg(iv_med=("iv", "median"),
                m_med=("m", "median"),
                S_med=("S", "median"),
                T_med=("T", "median"))
           .reset_index())

# ── Parabolic fit across all observations ─────────────────────────────────────
all_m = summary["m_med"].values
all_iv = summary["iv_med"].values
mask = np.isfinite(all_m) & np.isfinite(all_iv)
coeffs = np.polyfit(all_m[mask], all_iv[mask], 2)
p = np.poly1d(coeffs)
m_fit = np.linspace(all_m[mask].min(), all_m[mask].max(), 300)

print(f"\nParabolic fit  IV = {coeffs[0]:.4f}*m^2 + {coeffs[1]:.4f}*m + {coeffs[2]:.4f}")
print(f"ATM vol (m=0)  = {p(0):.4f}")
print(f"Skew  (dIV/dm at m=0) = {coeffs[1]:.4f}")
print(f"Smile curvature = {coeffs[0]:.4f}")

# ── Plot ───────────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Left: per-day scatter coloured by day
day_colors = {0: "#4e9af1", 1: "#f1914e", 2: "#4ef18d"}
for day, grp in summary.groupby("day"):
    axes[0].scatter(grp["m_med"], grp["iv_med"],
                    color=day_colors[day], s=50, alpha=0.75,
                    label=f"Day {day}")
axes[0].plot(m_fit, p(m_fit), "k--", lw=2, label="Parabola fit")
axes[0].axvline(0, color="gray", ls=":", lw=1)
axes[0].set_xlabel("Log-moneyness  m = ln(K/S) / √TTE", fontsize=11)
axes[0].set_ylabel("Implied Volatility (annualised-tick units)", fontsize=11)
axes[0].set_title("VEV Implied Volatility Smile — all days")
axes[0].legend()
axes[0].grid(alpha=0.3)

# Right: IV per strike over time (one dot per 1000-tick bin)
atm_k = sorted(options["K"].unique())
for k in atm_k:
    sub = opts_clean[opts_clean["K"] == k].sort_values("elapsed_ticks")
    valid = sub.dropna(subset=["iv"])
    if len(valid) < 2:
        continue
    label = f"K={int(k)}"
    axes[1].plot(valid["elapsed_ticks"], valid["iv"],
                 lw=1.2, marker="o", ms=3, label=label, alpha=0.8)
axes[1].set_xlabel("Elapsed ticks (1000-tick bins)")
axes[1].set_ylabel("Implied Volatility")
axes[1].set_title("IV per strike over time")
axes[1].legend(fontsize=7, ncol=2)
axes[1].grid(alpha=0.3)

plt.tight_layout()
out = os.path.join(os.path.dirname(__file__), "iv_smile.png")
plt.savefig(out, dpi=150)
print(f"\nSaved: {out}")
