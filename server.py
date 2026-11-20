#!/usr/bin/env python3
"""
colab-connect server - minimal HTTP bridge.

Endpoints (everything is JSON except /):
  GET  /                    tiny status/control page
  GET  /status              JSON status
  GET  /exec?cmd=...        run a shell command (GET variant)
  POST /exec                JSON {"cmd": "...", "timeout": 30} -> shell result
  POST /upload              multipart file upload -> ./uploads/
  GET  /download?path=...   download a file (or a zip of a directory)

Optional auth: set env BRIDGE_TOKEN. When set, every API call must include
`Authorization: Bearer <token>` (or `?token=<token>`).
"""
import argparse, io, json, os, re, subprocess, sys, time, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.abspath(os.getcwd())
UPLOAD_DIR = os.path.join(ROOT, "uploads")
START = time.time()
TOKEN = os.environ.get("BRIDGE_TOKEN", "").strip()


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def send_bytes(h, data, ctype, status=200, headers=None):
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(data)


def send_json(h, obj, status=200):
    send_bytes(h, json.dumps(obj).encode(), "application/json", status)


def safe_path(rel):
    p = os.path.realpath(os.path.join(ROOT, rel))
    if p == ROOT or p.startswith(ROOT + os.sep):
        return p
    return None


def multipart_fields(ctype, body):
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return {}
    boundary = m.group(1).encode()
    fields = {}
    for part in body.split(b"--" + boundary):
        if not part or part.startswith(b"--"):
            continue
        head, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = filename = None
        for line in head.split(b"\r\n"):
            line = line.decode("utf-8", "replace")
            nm = re.search(r'name="([^"]*)"', line)
            fm = re.search(r'filename="([^"]*)"', line)
            if nm:
                name = nm.group(1)
            if fm:
                filename = fm.group(1)
        if name:
            fields[(name, filename)] = content.rstrip(b"\r\n")
    return fields


class Handler(BaseHTTPRequestHandler):
    server_version = "colab-connect/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (now(), fmt % args))

    # ---------- helpers ----------
    def _authorized(self):
        if not TOKEN:
            return True
        qs = parse_qs(urlparse(self.path).query)
        auth = self.headers.get("Authorization", "")
        if auth == "Bearer " + TOKEN or qs.get("token", [""])[0] == TOKEN:
            return True
        send_json(self, {"error": "unauthorized"}, 401)
        return False

    def do_OPTIONS(self):
        send_bytes(self, b"", "text/plain")

    # ---------- GET ----------
    def do_GET(self):
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            return self._page()
        if parsed.path == "/status":
            return send_json(self, {
                "status": "ok",
                "hostname": os.uname().nodename,
                "cwd": ROOT,
                "pid": os.getpid(),
                "started": now(),
                "uptime_s": int(time.time() - START),
                "python": sys.version.split()[0],
                "auth_required": bool(TOKEN),
            })
        if parsed.path == "/exec":
            cmd = qs.get("cmd", [""])[0]
            timeout = int(qs.get("timeout", ["30"])[0])
            return self._exec(cmd, timeout)
        if parsed.path == "/download":
            return self._download(qs.get("path", [""])[0])
        send_json(self, {"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if parsed.path == "/exec":
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                return send_json(self, {"error": "invalid JSON"}, 400)
            cmd = payload.get("cmd", "")
            timeout = int(payload.get("timeout", 30))
            return self._exec(cmd, timeout)
        if parsed.path == "/upload":
            return self._upload(self.headers.get("Content-Type", ""), body)
        send_json(self, {"error": "not found"}, 404)

    # ---------- actions ----------
    def _exec(self, cmd, timeout):
        if not cmd.strip():
            return send_json(self, {"error": "empty cmd"}, 400)
        try:
            r = subprocess.run(cmd, shell=True, cwd=ROOT,
                               capture_output=True, text=True, timeout=timeout)
            return send_json(self, {
                "cmd": cmd,
                "code": r.returncode,
                "stdout": r.stdout,
                "stderr": r.stderr,
            })
        except subprocess.TimeoutExpired:
            return send_json(self, {
                "cmd": cmd, "code": -9,
                "stdout": "",
                "stderr": "timed out after %ss" % timeout,
            }, 504)

    def _download(self, rel):
        path = safe_path(rel)
        if not path or not os.path.exists(path):
            return send_json(self, {"error": "not found: " + rel}, 404)
        if os.path.isdir(path):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for base, _, files in os.walk(path):
                    for f in files:
                        full = os.path.join(base, f)
                        z.write(full, os.path.relpath(full, path))
            data = buf.getvalue()
            fname = os.path.basename(path.rstrip("/")) or "dir"
            return send_bytes(self, data, "application/zip", headers={
                "Content-Disposition": 'attachment; filename="%s.zip"' % fname})
        with open(path, "rb") as fh:
            data = fh.read()
        return send_bytes(self, data, "application/octet-stream", headers={
            "Content-Disposition": 'attachment; filename="%s"'
            % os.path.basename(path)})

    def _upload(self, ctype, body):
        fields = multipart_fields(ctype, body)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        saved, errors = [], []
        for (name, filename), content in fields.items():
            if not filename:
                continue
            fname = os.path.basename(filename)
            dest = os.path.join(UPLOAD_DIR, fname)
            try:
                with open(dest, "wb") as fh:
                    fh.write(content)
                saved.append(os.path.relpath(dest, ROOT))
            except OSError as e:
                errors.append({"file": fname, "error": str(e)})
        return send_json(self, {"saved": saved, "errors": errors})

    # ---------- page ----------
    def _page(self):
        html = """<!doctype html>
<html><head><meta charset="utf-8"><title>colab-connect</title>
<style>
 body{font-family:ui-monospace,Menlo,Consolas,monospace;max-width:720px;margin:40px auto;padding:0 16px;color:#222}
 input,textarea,button{font:inherit;padding:6px 8px;border:1px solid #999;border-radius:6px}
 button{background:#111;color:#fff;cursor:pointer}
 pre{background:#f5f5f5;border:1px solid #ddd;border-radius:8px;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-all}
 h1{font-size:22px}.muted{color:#777}
</style></head><body>
<h1>colab-connect bridge</h1>
<p class="muted">Minimal bridge: status, upload, download, and shell exec.</p>
<p>Status: <button onclick="st()">check</button> <span id="st"></span></p>
<p>Shell: <input id="cmd" size="50" placeholder="e.g. ls -la"
 onkeydown="if(event.key==='Enter')ex()"> <button onclick="ex()">run</button></p>
<pre id="out">&#8203;</pre>
<p>Upload: <input id="up" type="file" multiple> <button onclick="up()">send</button>
 <span class="muted">→ saved into ./uploads/</span></p>
<pre id="upr">&#8203;</pre>
<p>Download: append <code>?path=…</code> (file, or directory → zip)</p>
<script>
const s=(o,opt)=>fetch(o,opt).then(r=>r.json());
async function st(){const j=await s('/status');document.getElementById('st').textContent=JSON.stringify(j)}
async function ex(){const j=await s('/exec',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({cmd:document.getElementById('cmd').value})});
 document.getElementById('out').textContent=(j.stdout||'')+(j.stderr?'\n[stderr]\n'+j.stderr:'')+('\n[exit '+j.code+']');}
async function up(){const fd=new FormData();for(const f of document.getElementById('up').files)fd.append('file',f);
 const j=await s('/upload',{method:'POST',body:fd});document.getElementById('upr').textContent=JSON.stringify(j)}
</script></body></html>"""
        return send_bytes(self, html.encode(), "text/html; charset=utf-8")


def main():
    ap = argparse.ArgumentParser(description="colab-connect HTTP bridge")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print("[colab-connect] serving http://%s:%d  root=%s" % (a.host, a.port, ROOT), flush=True)
    if TOKEN:
        print("[colab-connect] auth enabled (Bearer token)", flush=True)
    else:
        print("[colab-connect] WARNING: no BRIDGE_TOKEN set -> anyone with the URL can act", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
