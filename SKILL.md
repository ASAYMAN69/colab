---
name: colab-bridge
description: Use a Google Colab GPU sandbox through a Cloudflare tunnel for heavy work (video rendering, ML runs, downloads, etc.). Triggers when the user wants GPU compute, video rendering, model training/inference, or shell access to a Colab runtime.
---

# Colab ⇄ Computer Bridge

This repo lets an AI agent (you) collaborate with a **Google Colab** runtime through a
**Cloudflare tunnel**. Colab provides free GPU machines (e.g. T4) that you can use for
GPU-heavy work such as video rendering, ML model training/inference, or any long-running
compute. The notebook starts a tiny HTTP server inside Colab and exposes it publicly over
HTTPS — which gives you shell execution, file upload, and file download in the Colab
environment.

## When to use

- The user asks for GPU work: video rendering, ML training/running, heavy compute.
- The user wants you to run commands or move files in their Colab session.
- The user wants a disposable remote sandbox with root-ish privileges (Colab user).

## Workflow

### 1. Get the user to open the notebook (as a GPU runtime)

Tell the user, in plain words:

> 1. Open this exact URL in a browser:
>    `https://colab.research.google.com/github/ASAYMAN69/colab/blob/main/colab_connect.ipynb`
> 2. Look at the **top-right corner** of the page. Find the **Connect** button and click
>    the **small dropdown arrow** next to it.
> 3. In the menu, select **Change runtime type** (some versions show it directly).
> 4. Under **Hardware accelerator**, choose **T4 GPU** and click **Save** (hit Enter).
> 5. In the first (only) code block — **cell 1** — click the **play/run** button (or
>    Shift+Enter). Wait until it prints a line that looks like:
>    `PUBLIC URL -> https://something-words.trycloudflare.com`
> 6. Copy that full URL and paste it back to me.

### 2. Receive the tunnel URL

The user pastes a URL like `https://tour-update-fell-fat.trycloudflare.com`. This is your
entry point. Verify it immediately with a quick `GET /status`, then proceed.

### 3. Use the Colab environment

All interaction goes over HTTPS with `curl` (or any HTTP client):

| Endpoint | Purpose | Example |
| --- | --- | --- |
| `GET /status` | Runtime info (hostname, cwd, python, uptime) | `curl -s $URL/status` |
| `POST /exec` | Run a shell command (JSON body) | `curl -s -X POST $URL/exec -H 'Content-Type: application/json' -d '{"cmd":"nvidia-smi"}'` |
| `GET /exec?cmd=…` | Same, GET variant | `curl -s "$URL/exec?cmd=ls -la"` |
| `POST /upload` | Upload a file (multipart, `file` field) | `curl -s -F "file=@./local.txt" $URL/upload` → saved to `uploads/` |
| `GET /download?path=…` | Download a file (or directory as zip) | `curl -sOJ "$URL/download?path=uploads/local.txt"` |

The command runs in `/content` (Colab's working dir) with the same privileges as the
notebook user — effectively a shell into the Colab VM.

### 4. Clean up when done

Tell the user to run **cell 2** (the stop block) in the notebook, or just close the
notebook tab — the tunnel dies with the runtime.

## Rules and guardrails

- **Always verify the URL first**: hit `/status` before trusting the tunnel.
- **Never assume auth**: check `auth_required` in `/status`. If it's `false`, anyone with
  the URL can execute shell commands. Warn the user and recommend `BRIDGE_TOKEN`.
- **Treat it as ephemeral**: the runtime dies when the notebook stops; don't rely on
  persistence across sessions.
- **Prefers curl with timeouts** (`-m 30` or so) — a hung request can stall you.
- **Transient hiccups happen**: a request occasionally returns nothing; retry once before
  assuming the tunnel is down.
- **GPU check**: to confirm acceleration, run `nvidia-smi` (or
  `python -c "import torch;print(torch.cuda.is_available())"` if PyTorch matters).
- **Security**: never paste the tunnel URL into public channels; it is a live shell.
