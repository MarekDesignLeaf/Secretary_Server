"""Generate the activity catalogue CSV with the system default Oxfordshire rates.

The rate model lives in secretary_clean/catalogue/default_rates.py — the same
defaults the backend serves wherever a tenant has no pricing override.

Run from the server repo root:
  uv run --python cpython-3.12-windows-x86_64 --with-requirements requirements.txt \
      python tools/oxfordshire_rates.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from secretary_clean.catalogue import default_rates  # noqa: E402
from secretary_clean.catalogue.source_parser import load_catalogue  # noqa: E402

OUT_PATH = Path(r"C:\Users\hutra\AndroidStudioProjects\secretary\SECRETARY_ACTIVITIES_CATALOGUE.csv")


def main() -> None:
    cat = load_catalogue()
    rows = 0
    with OUT_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["industry_code", "industry_name", "subtype_code", "subtype_name",
                    "activity_code", "activity_name", "default_pricing_method",
                    "rate_unit", "oxfordshire_rate_gbp", "available_pricing_methods"])
        for ind in cat.industries:
            for st in ind.subtypes:
                for a in st.activities:
                    method = a.default_pricing_method_code
                    w.writerow([ind.code, ind.name, st.code, st.name, a.code, a.name,
                                method, default_rates.RATE_UNITS.get(method, "GBP"),
                                default_rates.default_rate(ind.code, a.name, method),
                                "|".join(a.available_pricing_method_codes)])
                    rows += 1
    print(f"written: {OUT_PATH} rows={rows}")


if __name__ == "__main__":
    main()
