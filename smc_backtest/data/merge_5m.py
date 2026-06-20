import csv, pathlib, datetime, json, sys

OUT = pathlib.Path(__file__).parent / "BTCUSD_5M.csv"
new_bars = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)

existing = {}
if OUT.exists():
    with open(OUT, newline="") as f:
        for row in csv.DictReader(f):
            t = int(datetime.datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())
            existing[t] = row

for b in new_bars:
    dt = datetime.datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d %H:%M:%S")
    existing[b["time"]] = {"datetime": dt, "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b.get("volume", 0)}

rows = [existing[t] for t in sorted(existing.keys())]
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, ["datetime", "open", "high", "low", "close", "volume"])
    w.writeheader()
    w.writerows(rows)
print(f"Merged. Total bars now: {len(rows)}  range: {rows[0]['datetime']} -> {rows[-1]['datetime']}")
