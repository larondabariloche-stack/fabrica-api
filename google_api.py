#!/usr/bin/env python3
"""Helper Google API: refresh token + helper de lectura."""
import json, os, sys, urllib.request, urllib.parse

WS = os.path.expanduser("~/.openclaw/workspace")
TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", os.path.join(WS, "token.json"))

def load_token():
    with open(TOKEN_PATH) as f:
        return json.load(f)

def refresh_access():
    d = load_token()
    body = urllib.parse.urlencode({
        "refresh_token": d["refresh_token"],
        "client_id": d["client_id"],
        "client_secret": d["client_secret"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read().decode())
            access = tok["access_token"]
            d["token"] = access
            with open(TOKEN_PATH, "w") as f:
                json.dump(d, f, indent=2)
            return access
    except Exception as e:
        print("REFRESH ERROR:", e, file=sys.stderr)
        sys.exit(1)

def get_access():
    d = load_token()
    # si expiró, refrescar
    import time
    if float(d.get("expiry", 0)) < time.time() + 60:
        return refresh_access()
    return d["token"]

def api(url, method="GET", body=None):
    access = get_access()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + access)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

def sheets_get(spreadsheet_id, ranges, major_dimension="ROWS"):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchGet?ranges=" + "&ranges=".join(urllib.parse.quote(r) for r in ranges) + f"&majorDimension={major_dimension}"
    return api(url)

if __name__ == "__main__":
    # quick test
    print("access OK:", get_access()[:30], "...")
    print("sheets metadata:")
    import urllib.parse as up
    url = "https://sheets.googleapis.com/v4/spreadsheets/1X6wGVPj4WtlNNnglBwqzE5VWPEPMuMrRD4z6mxqA7nY?fields=properties.title,sheets.properties"
    r = api(url)
    print(r["properties"]["title"])
    for s in r["sheets"]:
        print(" -", s["properties"]["title"], s["properties"].get("gridProperties", {}))