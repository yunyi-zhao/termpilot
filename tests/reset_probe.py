import json, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def probe(base, headers, n_rounds=40):
    resets = other = n = 0
    t0 = time.time()
    for _ in range(n_rounds):
        for m, p, b in [('GET', '/api/sessions', None), ('GET', '/api/events', None),
                        ('POST', '/api/sessions/nonexist/mode', {'mode': 'auto'})]:
            n += 1
            req = urllib.request.Request(base + p, method=m, headers=headers)
            d = json.dumps(b).encode() if b is not None else None
            try:
                with urllib.request.urlopen(req, d, timeout=10) as r:
                    r.read()
            except urllib.error.HTTPError:
                pass
            except (ConnectionResetError, ConnectionAbortedError):
                resets += 1
            except Exception as e:
                other += 1
    return 'requests %d resets %d other %d %.1fs' % (n, resets, other, time.time() - t0)

class H(BaseHTTPRequestHandler):
    def _ok(self):
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(b'[]')
    do_GET = _ok
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0))); self._ok()
    def log_message(self, *a): pass

if __name__ == '__main__':
    cfg = json.load(open('config.json'))
    H_ = {'X-Token': cfg['token'], 'Content-Type': 'application/json'}
    srv = ThreadingHTTPServer(('127.0.0.1', 8791), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print('stdlib server 127.0.0.1:8791 :', probe('http://127.0.0.1:8791', H_))
    print('termpilot 127.0.0.1:8787     :', probe('http://127.0.0.1:8787', H_))
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from termpilot_cli_client import default_base
    base = default_base(cfg)
    if '127.0.0.1' not in base:
        print('termpilot %s :' % base, probe(base, H_))
