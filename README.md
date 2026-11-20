# colab-connect

Minimal bridge between a Colab/notebook runtime and an AI agent, over a
Cloudflare HTTPS tunnel.

```
┌──────────────┐   run notebook   ┌───────────────┐   cloudflared   ┌──────────────────────┐
│ Google Colab │ ───────────────► │  server.py    │ ───────────────► │ https://….trycloudflare│
│ (or your box)│                  │ 127.0.0.1:8787│                  │ .com (public HTTPS)   │
└──────────────┘                  └───────────────┘                  └──────────┬───────────┘
                                                                                 │ paste to AI
                                                                                 ▼
                                                                     ┌──────────────────────┐
                                                                     │ AI (this agent)      │
                                                                     │ upload / download /   │
                                                                     │ shell exec via HTTP   │
                                                                     └──────────────────────┘
```

## What it does

`colab_connect.ipynb` (when run, in order):

1. Makes `server.py` available — clones `REPO_URL` if you run it outside this repo, otherwise uses the file next to the notebook.
2. Starts a stdlib-only HTTP server (`server.py`) on `127.0.0.1:8787`.
3. Installs `cloudflared` (single binary from GitHub releases) if it's not on `PATH` or at `~/.local/bin/cloudflared`.
4. Opens a free Cloudflare quick tunnel and prints the public `https://….trycloudflare.com` URL.
5. You copy that URL and paste it to the AI.

## Server API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Tiny control page (status / shell box / upload form) |
| `GET /status` | JSON: hostname, cwd, pid, uptime, python version |
| `POST /exec` | JSON `{"cmd": "ls -la", "timeout": 30}` → `{code, stdout, stderr}` |
| `GET /exec?cmd=ls` | Same, GET variant |
| `POST /upload` | Multipart `file` field(s) → saved into `./uploads/` |
| `GET /download?path=<file>` | Download a file |
| `GET /download?path=<dir>` | Download the directory as a zip |

Examples with curl:

```bash
URL=https://xxxx-xxx.trycloudflare.com

curl -s $URL/status
curl -s -X POST $URL/exec -H 'Content-Type: application/json' \
     -d '{"cmd":"ls -la"}'
curl -s -F "file=@./notes.txt" $URL/upload
curl -sOJ $URL/download?path=uploads/notes.txt
curl -sOJ $URL/download?path=uploads
```

## Security (read this)

- By default the server has **no authentication**. Anyone who obtains the URL can
  run arbitrary shell commands as the notebook's user, upload, and download files.
  A Cloudflare URL is public — treat it as a live root shell.
- **Set `BRIDGE_TOKEN`** in the notebook config cell before starting, and pass
  `Authorization: Bearer <token>` (or `?token=`) on every request. The server
  then rejects unauthenticated calls.
- Stop the notebook when done — the tunnel dies with the `cloudflared` process.
- Keep the notebook's output private; the URL is printed for you to copy.

## Running

Local Jupyter: open `colab_connect.ipynb` in this repo and run all cells.
Google Colab: upload the notebook, set `REPO_URL` to a cloneable copy of this
repo (push it to GitHub first), then run all cells.

## Notes

- GitHub releases event for cloudflared: `cloudflared-linux-amd64` /
  `cloudflared-linux-arm64` (auto-detected).
- Quick tunnels can be rate-limited; if no URL appears within 90s, wait a minute
  and rerun the tunnel cell.
- Commands run in the repo root (`server.py`'s working directory) with the same
  privileges as the notebook user.
