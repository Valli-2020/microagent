"""Web tools for micro: SSRF-checked URL validation plus crw search/scrape (stdlib only)."""
import ipaddress, json, socket, urllib.error, urllib.request
from urllib.parse import urlparse


class WebError(Exception):
    pass


def _bad(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (not ip.is_global or ip.is_multicast
            or ip in ipaddress.ip_network("100.64.0.0/10"))  # CGNAT/Tailnet: not is_private


def check_url(url):
    # shortcut: DNS rebinding not handled (check resolves, crw resolves again itself); upgrade if we ever fetch directly
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("only http/https URLs are allowed")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 80, proto=socket.IPPROTO_TCP)
    except OSError:
        raise ValueError("host does not resolve")
    for info in infos:
        if _bad(ipaddress.ip_address(info[4][0].split("%")[0])):
            raise ValueError("host resolves to a non-public address")


def _post(crw, path, payload):
    h = {"Content-Type": "application/json"}
    if crw.get("key"):
        h["Authorization"] = "Bearer " + crw["key"]
    req = urllib.request.Request(crw["url"].rstrip("/") + path, json.dumps(payload).encode(), h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise WebError("crw: bad or missing key" if e.code == 401 else f"crw: HTTP {e.code}") from None
    except Exception as e:
        raise WebError(f"crw: {type(e).__name__} (unreachable or bad response)") from None
    if not isinstance(d, dict) or not d.get("success", True):
        raise WebError("crw: request failed")
    return d.get("data")


def search(crw, query):
    data = _post(crw, "/v1/search", {"query": query, "limit": 5})
    if isinstance(data, dict):
        data = data.get("web") or []
    out = []
    for i, r in enumerate((data or [])[:5], 1):
        snip = " ".join((r.get("description") or r.get("snippet") or "").split())
        out.append(f"{i}. {r.get('title') or '(no title)'} - {r.get('url', '')}\n  {snip}")
    return "\n".join(out) or "no results"


def scrape(crw, url):
    check_url(url)
    data = _post(crw, "/v1/scrape", {"url": url, "formats": ["markdown"]})
    md = (data or {}).get("markdown") or ""
    return md[:4000] + "\n[truncated]" if len(md) > 4000 else md or "(empty page)"


def selftest():
    import http.server, threading
    for u in ["http://localhost/", "http://127.0.0.1/", "http://[::1]/", "http://10.0.0.1/", "http://192.168.1.1/",
              "http://172.16.0.1/", "http://100.64.0.1/", "http://169.254.169.254/", "http://0.0.0.0/",
              "http://224.0.0.1/", "http://[::ffff:127.0.0.1]/", "http://[::ffff:169.254.169.254]/",
              "ftp://example.com/", "file:///etc/passwd", "http:///x", "nonsense"]:
        try:
            check_url(u)
        except ValueError:
            continue
        raise AssertionError("not blocked: " + u)
    check_url("http://93.184.216.34/")  # public literal, no DNS needed

    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            seen[self.path] = (json.loads(self.rfile.read(n)), self.headers.get("Authorization"))
            if self.headers.get("Authorization") != "Bearer k":
                self.send_response(401); self.end_headers(); return
            if self.path == "/v1/search":
                body = {"success": True, "data": {"web": [{"title": f"T{i}", "url": f"http://x/{i}", "description": "a\nb"} for i in range(7)]}}
            else:
                body = {"success": True, "data": {"markdown": "x" * 5000}}
            b = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        log_message = lambda *a: None

    s = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    crw = {"url": f"http://127.0.0.1:{s.server_port}", "key": "k"}
    r = search(crw, "pi")
    assert r.startswith("1. T0 - http://x/0\n  a b") and r.count("\n") == 9 and "T5" not in r, r
    assert seen["/v1/search"][0] == {"query": "pi", "limit": 5}
    try:
        search({**crw, "key": "secret"}, "q"); raise AssertionError
    except WebError as e:
        assert str(e) == "crw: bad or missing key" and "secret" not in str(e)
    try:
        scrape(crw, "http://127.0.0.1/"); raise AssertionError  # SSRF check runs before any request
    except ValueError:
        pass
    assert "/v1/scrape" not in seen
    import unittest.mock as m
    with m.patch(f"{__name__}.check_url" if __name__ != "__main__" else "__main__.check_url"):
        t = scrape(crw, "http://example.com")
    assert t.endswith("[truncated]") and len(t) < 4020
    s.shutdown()
    try:
        search({"url": "http://127.0.0.1:9", "key": "k"}, "q"); raise AssertionError
    except WebError as e:
        assert "\n" not in str(e)
    print("web ok")


if __name__ == "__main__":
    selftest()
