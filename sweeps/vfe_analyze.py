import csv, os, math, statistics as st
from collections import Counter, defaultdict

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "A-Historical Data", "Round 3")
PRODUCT = "VELVETFRUIT_EXTRACT"

def load_prices():
    rows = []
    for day in (0, 1, 2):
        path = os.path.join(BASE, f"prices_round_3_day_{day}.csv")
        with open(path, newline="") as f:
            rdr = csv.DictReader(f, delimiter=";")
            for r in rdr:
                if r["product"] != PRODUCT: continue
                def i(v):
                    try: return int(v)
                    except: return None
                def fl(v):
                    try: return float(v)
                    except: return None
                rows.append({
                    "day": int(r["day"]),
                    "ts": int(r["timestamp"]),
                    "b1": i(r["bid_price_1"]), "bv1": i(r["bid_volume_1"]),
                    "b2": i(r["bid_price_2"]), "bv2": i(r["bid_volume_2"]),
                    "b3": i(r["bid_price_3"]), "bv3": i(r["bid_volume_3"]),
                    "a1": i(r["ask_price_1"]), "av1": i(r["ask_volume_1"]),
                    "a2": i(r["ask_price_2"]), "av2": i(r["ask_volume_2"]),
                    "a3": i(r["ask_price_3"]), "av3": i(r["ask_volume_3"]),
                    "mid": fl(r["mid_price"]),
                })
    return rows

def load_trades():
    rows = []
    for day in (0, 1, 2):
        path = os.path.join(BASE, f"trades_round_3_day_{day}.csv")
        with open(path, newline="") as f:
            rdr = csv.DictReader(f, delimiter=";")
            for r in rdr:
                if r["symbol"] != PRODUCT: continue
                rows.append({
                    "ts": int(r["timestamp"]),
                    "price": float(r["price"]),
                    "qty": int(r["quantity"]),
                })
    return rows

prices = load_prices()
trades = load_trades()

print(f"price rows: {len(prices)}")
print(f"trade rows: {len(trades)}")
if not prices:
    raise SystemExit

by_day = defaultdict(list)
for r in prices: by_day[r["day"]].append(r)
for d in sorted(by_day):
    arr = by_day[d]
    mids = [x["mid"] for x in arr if x["mid"] is not None]
    print(f"day {d}: n={len(arr)}, first_ts={arr[0]['ts']}, last_ts={arr[-1]['ts']}, "
          f"mid min={min(mids):.1f}, max={max(mids):.1f}, mean={sum(mids)/len(mids):.2f}, "
          f"first_mid={mids[0]:.2f}, last_mid={mids[-1]:.2f}")

mids = [x["mid"] for x in prices if x["mid"] is not None]
print(f"\nOverall mid: mean={sum(mids)/len(mids):.3f}, stdev={st.pstdev(mids):.3f}, "
      f"min={min(mids):.1f}, max={max(mids):.1f}, n={len(mids)}")

# Returns
def returns(seq):
    return [seq[i]-seq[i-1] for i in range(1, len(seq))]

rets_all = []
for d in sorted(by_day):
    arr = by_day[d]
    mids_d = [x["mid"] for x in arr]
    rets_all.extend(returns(mids_d))
print(f"Returns: n={len(rets_all)} mean={sum(rets_all)/len(rets_all):.5f}, "
      f"stdev={st.pstdev(rets_all):.4f}, min={min(rets_all):.2f}, max={max(rets_all):.2f}")
# abs return distribution
abs_r = [abs(x) for x in rets_all]
srt = sorted(abs_r)
def pct(p): return srt[int(p*len(srt))]
print(f"|ret| p50={pct(0.5):.3f} p90={pct(0.9):.3f} p99={pct(0.99):.3f} max={srt[-1]:.3f}")
zeros = sum(1 for x in rets_all if x==0)
print(f"zero-returns: {zeros}/{len(rets_all)} = {zeros/len(rets_all)*100:.1f}%")

# Autocorrelation of returns
def ac(seq, lag):
    mu = sum(seq)/len(seq)
    var = sum((x-mu)**2 for x in seq)
    if var==0: return 0
    num = sum((seq[i]-mu)*(seq[i-lag]-mu) for i in range(lag, len(seq)))
    return num/var
print("\nAutocorr of returns:")
for l in (1,2,3,5,10,20,50,100):
    print(f"  ac({l}) = {ac(rets_all,l):+.4f}")

# Autocorrelation of mids (for mean-reversion detection via OU half-life)
mu_mid = sum(mids)/len(mids)
dev = [m - mu_mid for m in mids]
ac1_mid = 0
num = sum(dev[i]*dev[i-1] for i in range(1,len(dev)))
den = sum(d*d for d in dev)
if den > 0: ac1_mid = num/den
print(f"AC(1) of mid-levels: {ac1_mid:.5f}  => OU half-life ~ {math.log(2)/max(1e-9,-math.log(abs(ac1_mid))) if ac1_mid<1 else 'inf':.1f} ticks")

# Spread analysis
spreads = []
for r in prices:
    if r["a1"] is not None and r["b1"] is not None:
        spreads.append(r["a1"]-r["b1"])
sp_ct = Counter(spreads)
print(f"\nSpread: n={len(spreads)} mean={sum(spreads)/len(spreads):.3f} min={min(spreads)} max={max(spreads)}")
for s,c in sorted(sp_ct.items())[:15]:
    print(f"  spread={s}: {c} ({c/len(spreads)*100:.1f}%)")

# Top-of-book depth
bv = [r["bv1"] for r in prices if r["bv1"] is not None]
av = [abs(r["av1"]) for r in prices if r["av1"] is not None]
print(f"\nTop-of-book bid_vol: mean={sum(bv)/len(bv):.2f} median={sorted(bv)[len(bv)//2]} max={max(bv)}")
print(f"Top-of-book ask_vol: mean={sum(av)/len(av):.2f} median={sorted(av)[len(av)//2]} max={max(av)}")

# Book-level frequency - how many levels typically present
def has(v): return 1 if v is not None else 0
lv_bid = Counter((has(r["b1"]), has(r["b2"]), has(r["b3"])) for r in prices)
lv_ask = Counter((has(r["a1"]), has(r["a2"]), has(r["a3"])) for r in prices)
print("Bid levels pattern:", lv_bid.most_common(5))
print("Ask levels pattern:", lv_ask.most_common(5))

# Distance bid1->bid2, ask1->ask2
d_b = [r["b1"]-r["b2"] for r in prices if r["b1"] is not None and r["b2"] is not None]
d_a = [r["a2"]-r["a1"] for r in prices if r["a1"] is not None and r["a2"] is not None]
if d_b:
    dbc = Counter(d_b)
    print("b1-b2 gap dist:", dbc.most_common(10))
if d_a:
    dac = Counter(d_a)
    print("a2-a1 gap dist:", dac.most_common(10))

# Bot quoting: full "outer" spread (widest price on each side that is actually quoted)
outer_spreads = []
for r in prices:
    bids = [x for x in (r["b1"],r["b2"],r["b3"]) if x is not None]
    asks = [x for x in (r["a1"],r["a2"],r["a3"]) if x is not None]
    if bids and asks:
        outer_spreads.append(max(asks)-min(bids))
os_ct = Counter(outer_spreads)
print("Outer spread dist (top):", os_ct.most_common(10))

# Trade analysis
if trades:
    tps = [t["price"] for t in trades]
    tqs = [t["qty"] for t in trades]
    print(f"\nTrades: n={len(trades)} mean_px={sum(tps)/len(tps):.2f} mean_qty={sum(tqs)/len(tqs):.2f} "
          f"max_qty={max(tqs)} total_vol={sum(tqs)}")
    qct = Counter(tqs)
    print("Trade qty dist:", qct.most_common(10))

# Order book imbalance -> next-tick return
# Build alignment: mid series and bv1/av1 series per day
import statistics
def pearson(xs, ys):
    n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
    num=sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    dx=math.sqrt(sum((x-mx)**2 for x in xs))
    dy=math.sqrt(sum((y-my)**2 for y in ys))
    if dx==0 or dy==0: return 0
    return num/(dx*dy)

imb_list = []; nxt_ret = []
for d in sorted(by_day):
    arr = by_day[d]
    for i in range(1, len(arr)-1):
        bv = arr[i]["bv1"]; av = arr[i]["av1"]; m = arr[i]["mid"]; mn = arr[i+1]["mid"]
        if None in (bv, av, m, mn): continue
        av_ = abs(av)
        if bv+av_ == 0: continue
        imb = (bv-av_)/(bv+av_)
        imb_list.append(imb); nxt_ret.append(mn-m)
print(f"\nImbalance->next-ret corr: {pearson(imb_list, nxt_ret):+.4f} (n={len(imb_list)})")

# Volatility by regime: rolling 100-tick stdev of returns
print("\nRolling 100-tick stdev of returns:")
for d in sorted(by_day):
    mids_d = [x['mid'] for x in by_day[d]]
    r_d = returns(mids_d)
    chunks = [r_d[i:i+100] for i in range(0, len(r_d), 100)]
    vols = [st.pstdev(c) for c in chunks if len(c)==100]
    if vols:
        print(f"  day {d}: mean={sum(vols)/len(vols):.3f} min={min(vols):.3f} max={max(vols):.3f}")

# Drift/trend test per day
print("\nLinear trend slope of mid vs tick:")
for d in sorted(by_day):
    arr = by_day[d]
    xs = list(range(len(arr))); ys = [x['mid'] for x in arr]
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    sxy=sum((xs[i]-mx)*(ys[i]-my) for i in range(len(xs)))
    sxx=sum((x-mx)**2 for x in xs)
    slope = sxy/sxx if sxx else 0
    ssr = sum((ys[i] - (my + slope*(xs[i]-mx)))**2 for i in range(len(xs)))
    sst = sum((y-my)**2 for y in ys)
    r2 = 1-ssr/sst if sst else 0
    print(f"  day {d}: slope_per_tick={slope:.6f}  R^2={r2:.4f}  total_drift={slope*len(xs):.2f}")
