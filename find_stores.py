"""Show which Target stores the IDs in config.json really are, and, when
Target allows it, the stores nearest the zip.

Run it when picking which stores to watch: python3 find_stores.py
"""

import json
import os
import urllib.parse

from checker import REDSKY, REDSKY_KEY, dig, http_json

here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "config.json")))

print(f"Configured stores, as Target names them:")
tcin = cfg["target_products"][0]["tcin"]
for store in cfg["target_stores"]:
    params = {"key": REDSKY_KEY, "channel": "WEB", "tcins": tcin,
              "store_id": store["id"], "required_store_id": store["id"],
              "has_required_store_id": "true", "zip": cfg["zip"], "state": cfg["state"],
              "latitude": cfg["latitude"], "longitude": cfg["longitude"], "page": "/p/-"}
    try:
        data = http_json(f"{REDSKY}/product_summary_with_fulfillment_v1?{urllib.parse.urlencode(params)}")
    except Exception as exc:
        print(f"  {store['id']}: lookup failed ({exc})")
        continue
    for opt in dig(data, "data", "product_summaries", 0, "fulfillment", "store_options", default=[]) or []:
        info = {k: v for k, v in opt.items() if not isinstance(v, (dict, list))}
        print(f"  {store['id']} ({store['name']}): {json.dumps(info)}")

print(f"\nTarget stores nearest {cfg['zip']}:")
params = {"key": REDSKY_KEY, "channel": "WEB", "place": cfg["zip"], "limit": 8, "within": 25}
try:
    data = http_json(f"{REDSKY}/nearby_stores_v1?{urllib.parse.urlencode(params)}")
except Exception as exc:
    print(f"  Target refused the store search from here ({exc}).")
    data = {}
for s in dig(data, "data", "nearby_stores", "stores", default=[]) or []:
    addr = s.get("mailing_address") or {}
    print(f"  {s.get('store_id')}\t{s.get('distance')} mi\t{s.get('location_name')}\t"
          f"{addr.get('address_line1')}, {addr.get('city')} {addr.get('postal_code')}")
