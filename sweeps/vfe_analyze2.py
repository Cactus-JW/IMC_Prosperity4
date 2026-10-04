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

prices = load_prices()

# Check the dominant bot quote structure: is it two-tier bot pair?
# spread=5 case: outer spread =6 means one side has a tighter layer of 2 and other of 3? or symmetric 2+1+2? Let's see.
# Actually outer spread is 6 (75%) or 7 (25%). Top spread is 5 (74%) or 6 (18%).

# Look at specific book patterns when 2 levels exist on each side
patterns = Counter()
for r in prices:
    bids = [(r["b1"], r["bv1"]), (r["b2"], r["bv2"]), (r["b3"], r["bv3"])]
    asks = [(r["a1"], r["av1"]), (r["a2"], r["av2"]), (r["a3"], r["av3"])]
    bids = [(p,v) for p,v in bids if p is not None]
    asks = [(p,v) for p,v in asks if p is not None]
    if len(bids)==2 and len(asks)==2:
        # normalize: distances from top
        b_top, a_top = bids[0][0], asks[0][0]
        b_gap = b_top - bids[1][0]
        a_gap = asks[1][0] - a_top
        s = a_top - b_top
        bv1, bv2 = bids[0][1], bids[1][1]
        av1, av2 = abs(asks[0][1]), abs(asks[1][1])
        patterns[(s, b_gap, a_gap)] += 1

print("Common (top_spread, b_gap, a_gap) patterns (2-level books):")
for k,c in patterns.most_common(10):
    print(" ", k, c)

# Volume at top vs second level when 2-layer
vol_top_vs_2 = []
for r in prices:
    bids = [(r["b1"], r["bv1"]), (r["b2"], r["bv2"]), (r["b3"], r["bv3"])]
    asks = [(r["a1"], r["av1"]), (r["a2"], r["av2"]), (r["a3"], r["av3"])]
    bids = [(p,v) for p,v in bids if p is not None]
    asks = [(p,v) for p,v in asks if p is not None]
    if len(bids)>=2 and len(asks)>=2:
        vol_top_vs_2.append((bids[0][1], bids[1][1], abs(asks[0][1]), abs(asks[1][1])))
if vol_top_vs_2:
    bv1m = sum(x[0] for x in vol_top_vs_2)/len(vol_top_vs_2)
    bv2m = sum(x[1] for x in vol_top_vs_2)/len(vol_top_vs_2)
    av1m = sum(x[2] for x in vol_top_vs_2)/len(vol_top_vs_2)
    av2m = sum(x[3] for x in vol_top_vs_2)/len(vol_top_vs_2)
    print(f"2-level book avg vols — bid1={bv1m:.2f} bid2={bv2m:.2f} ask1={av1m:.2f} ask2={av2m:.2f}")

# Does a "big" quote (25 at level-1) mark the fair-side? Compare bv1 magnitudes
bv1s = Counter()
for r in prices:
    if r["bv1"] is not None:
        bv1s[r["bv1"]] += 1
print("bv1 size distribution (top 15):", bv1s.most_common(15))

# Check "thin book" vs "thick book" detection: are spread=5 cases tight/thin and 6 cases wider/thicker?
# Actually we want to know: when spread=5, what's common bv1 size?
by_sp = defaultdict(list)
for r in prices:
    if r["a1"] is not None and r["b1"] is not None:
        sp = r["a1"]-r["b1"]
        by_sp[sp].append((r["bv1"], abs(r["av1"])))
for sp in sorted(by_sp):
    arr = by_sp[sp]
    n = len(arr)
    if n < 10: continue
    abv = sum(x[0] for x in arr)/n
    aav = sum(x[1] for x in arr)/n
    print(f"spread={sp}: n={n} avg bv1={abv:.2f} avg av1={aav:.2f}")

# Test: is spread-cross opportunity present? i.e. ever a1 <= b1? Should not but confirm
crossed = sum(1 for r in prices if r["a1"] is not None and r["b1"] is not None and r["a1"] <= r["b1"])
print(f"crossed books: {crossed}")

# What price changes drive next-tick moves - reversal vs momentum
# Build a per-tick feature: when mid moved +X this tick, did next tick move -X?
rets = []
for d in sorted(set(r["day"] for r in prices)):
    arr = [r for r in prices if r["day"]==d]
    mids_d = [x["mid"] for x in arr]
    rets.extend([mids_d[i]-mids_d[i-1] for i in range(1, len(mids_d))])

# Stratify next-ret by prev-ret
buckets = defaultdict(list)
for i in range(1, len(rets)):
    prev = rets[i-1]
    nxt = rets[i]
    # bucket by sign
    sgn = 1 if prev>0 else (-1 if prev<0 else 0)
    buckets[sgn].append(nxt)
for s in sorted(buckets):
    a = buckets[s]
    if not a: continue
    print(f"prev_sgn={s:+d}: n={len(a)} mean_next_ret={sum(a)/len(a):+.4f}")

# Half-life check via AR(1) of deviations from mean
import math
mids = [x["mid"] for x in prices]
mu = sum(mids)/len(mids)
dev = [m-mu for m in mids]
num = sum(dev[i]*dev[i-1] for i in range(1,len(dev)))
den = sum(d*d for d in dev)
phi = num/den
if 0 < phi < 1:
    hl = math.log(2)/(-math.log(phi))
    print(f"AR(1) phi={phi:.5f}  half-life={hl:.1f} ticks")

# Check if mid tends to round to .0 vs .5
halfs = sum(1 for m in mids if (m*2) % 2 == 1)
print(f"mids ending .5: {halfs}/{len(mids)} = {halfs/len(mids)*100:.1f}%")

# Window regression: does imbalance signal get stronger over multiple ticks?
import math
def corr(xs, ys):
    n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
    num=sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    dx=math.sqrt(sum((x-mx)**2 for x in xs))
    dy=math.sqrt(sum((y-my)**2 for y in ys))
    if dx==0 or dy==0: return 0
    return num/(dx*dy)

by_day2 = defaultdict(list)
for r in prices: by_day2[r["day"]].append(r)

for k in (1,3,5,10,20):
    xs=[]; ys=[]
    for d in sorted(by_day2):
        arr = by_day2[d]
        for i in range(0, len(arr)-k):
            bv = arr[i]["bv1"]; av = arr[i]["av1"]; m = arr[i]["mid"]; m2 = arr[i+k]["mid"]
            if None in (bv, av, m, m2): continue
            avx = abs(av)
            if bv+avx==0: continue
            xs.append((bv-avx)/(bv+avx))
            ys.append(m2-m)
    print(f"imbalance(t)->ret over {k} ticks: corr={corr(xs,ys):+.4f}")
