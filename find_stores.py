"""Print the Target stores nearest the zip in config.json, with their IDs.

Run it when picking which stores to watch: python3 find_stores.py
"""

import json
import os
import urllib.parse

from checker import REDSKY, REDSKY_KEY, dig, http_json

here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "config.json")))
params = {"key": REDSKY_KEY, "channel": "WEB", "place": cfg["zip"], "limit": 8, "within": 25}
data = http_json(f"{REDSKY}/nearby_stores_v1?{urllib.parse.urlencode(params)}")
stores = dig(data, "data", "nearby_stores", "stores", default=None)
if not stores:
    print(json.dumps(data, indent=2)[:4000])
for s in stores or []:
    addr = s.get("mailing_address") or {}
    print(f"{s.get('store_id')}\t{s.get('distance')} mi\t{s.get('location_name')}\t"
          f"{addr.get('address_line1')}, {addr.get('city')} {addr.get('postal_code')}")
