"""E2E test: serve saved Apple HTML fixtures over local HTTP and run the real
main() through the SDK's local storage emulation.

One scenario per process (the Actor context calls sys.exit on exit):
    python tests/test_e2e.py full      # full run, us+gb (identical fixtures)
    python tests/test_e2e.py delta     # delta rerun -> nothing new
    python tests/test_e2e.py delta404  # delta with a 404 storefront mixed in

Env: no_proxy="localhost,127.0.0.1" APIFY_LOCAL_STORAGE_DIR=<dir>
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
FIX = os.path.join(ROOT, "tests", "fixtures")
CACHE = {}
PAGE_NAMES = {None: "whatsapp-us-seeall.html", "iphone": "whatsapp-us-iphone.html",
              "mac": "whatsapp-us-mac.html"}


def page_for(query):
    qs = parse_qs(query)
    if qs.get("see-all") != ["reviews"]:
        return None
    name = PAGE_NAMES.get(qs.get("platform", [None])[0])
    if name not in CACHE:
        with open(os.path.join(FIX, name), encoding="utf-8") as f:
            CACHE[name] = f.read()
    return CACHE[name]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/us/app/id310633997", "/gb/app/id310633997"):
            body = page_for(u.query)
            if body is None:
                self.send_response(404); self.end_headers(); return
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        # /xx/... -> 404 (app not in storefront)
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


SCENARIOS = {
    "full": {"appId": "310633997", "countries": ["us", "gb"], "mode": "full"},
    "delta": {"appId": "310633997", "countries": ["us", "gb"], "mode": "delta"},
    "delta404": {"appId": "310633997", "countries": ["us", "xx"], "mode": "delta"},
}


def main():
    scenario = sys.argv[1]
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["REVIEW_FIREHOSE_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"

    storage = os.environ["APIFY_LOCAL_STORAGE_DIR"]
    kvs = os.path.join(storage, "key_value_stores", "default")
    os.makedirs(kvs, exist_ok=True)
    with open(os.path.join(kvs, "INPUT.json"), "w") as f:
        json.dump(SCENARIOS[scenario], f)

    import asyncio
    import main as M  # noqa: E402

    def ds_snapshot():
        d = os.path.join(storage, "datasets", "default")
        if not os.path.isdir(d):
            return set()
        return {fn for fn in os.listdir(d)
                if fn.endswith(".json") and fn != "__metadata__.json"}

    before = ds_snapshot()
    try:
        asyncio.run(M.main())
    except SystemExit as e:
        assert e.code == 0, f"actor exited with code {e.code}"
    after = ds_snapshot()
    new_files = after - before

    ds_dir = os.path.join(storage, "datasets", "default")
    items = []
    if os.path.isdir(ds_dir):
        for fn in sorted(os.listdir(ds_dir)):
            if fn == "__metadata__.json" or not fn.endswith(".json"):
                continue
            with open(os.path.join(ds_dir, fn), encoding="utf-8") as f:
                items.append(json.load(f))
    state_path = os.path.join(
        storage, "key_value_stores", "review-firehose",
        "review-firehose-state-310633997")
    if not os.path.exists(state_path):
        state_path += ".json"
    state = json.load(open(state_path))
    ids = [i["reviewId"] for i in items]
    print(f"SCENARIO={scenario} dataset_items={len(items)} unique_ids={len(set(ids))} "
          f"seen={len(state['seen'])} ratings={sorted(state['ratings'])}")

    if scenario == "full":
        assert len(items) == 30, f"expected 30 unique (us+gb dedup), got {len(items)}"
        assert len(set(ids)) == 30
        assert len(new_files) == 30, f"full run must write 30 items, wrote {len(new_files)}"
        assert len(state["seen"]) == 30
        assert sorted(state["ratings"]) == ["gb", "us"]
        assert state["ratings"]["us"]["ratingAverage"] == 4.7
        # every dataset item has the required schema fields
        for it in items:
            assert all(it.get(k) for k in ("reviewId", "appId", "text", "updated")), it
            assert isinstance(it["rating"], int)
    elif scenario == "delta":
        # The meaningful assertions: this run wrote nothing new, and the
        # persistent named-KVS state still holds all 30 seen IDs.
        assert len(new_files) == 0, f"delta rerun must push nothing, pushed {len(new_files)}"
        assert len(state["seen"]) == 30, "seen set must be unchanged"
    elif scenario == "delta404":
        assert len(new_files) == 0, "404 storefront must not break the run or push"
        assert len(state["seen"]) == 30
    print(f"E2E {scenario} PASSED")


if __name__ == "__main__":
    main()
