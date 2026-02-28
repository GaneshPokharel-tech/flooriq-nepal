import csv
import requests
from bs4 import BeautifulSoup
from pathlib import Path

URL = "https://www.merolagani.com/CompanyList.aspx"
OUT = Path("data/meta/symbol_sector.csv")

HEADERS = {
    "User-Agent": "flooriq-nepal/0.1 (+https://github.com/GaneshPokharel-tech/flooriq-nepal)"
}

def clean(s: str) -> str:
    return (s or "").strip()

def main():
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    mappings = {}

    tables = soup.find_all("table")
    for tbl in tables:
        header = tbl.find("tr")
        if not header:
            continue
        th_text = " ".join([clean(x.get_text(" ")) for x in header.find_all(["th", "td"])])
        if "Symbol" not in th_text:
            continue

        sector = "Unknown"
        prev = tbl
        for _ in range(25):
            prev = prev.find_previous()
            if prev is None:
                break
            if prev.name in ("h2", "h3", "h4"):
                t = clean(prev.get_text(" "))
                if t:
                    sector = t
                    break

        for tr in tbl.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue
            sym = clean(tds[0].get_text(" ")).upper()
            if sym and sym != "SYMBOL":
                mappings[sym] = sector

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "sector"])
        for sym in sorted(mappings.keys()):
            w.writerow([sym, mappings[sym]])

    print(f"OK: wrote {len(mappings)} symbols to {OUT}")

if __name__ == "__main__":
    main()























