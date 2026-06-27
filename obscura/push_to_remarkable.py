#!/usr/bin/env python3
"""Push a PDF to reMarkable 2 over SSH."""
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from pdf import make_pdf

REMARKABLE_HOST = "remarkable"  # hostname or IP
REMARKABLE_USER = "root"
REMARKABLE_PASS = "xxxxxxxxxxxx"  # SSH password (set a key instead if you can)
XOCHITL_DIR = "/home/root/.local/share/remarkable/xochitl"



def list_folders() -> dict[str, str]:
    """Return {folder_name: uuid} for all CollectionType items on the device."""
    result = subprocess.run([
        "sshpass", "-p", REMARKABLE_PASS,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{REMARKABLE_USER}@{REMARKABLE_HOST}",
        f"for f in {XOCHITL_DIR}/*.metadata; do echo \"__SEP__$f\"; cat \"$f\"; done",
    ], capture_output=True, text=True, check=True)
    folders = {}
    current_uid = None
    current_json = []
    for line in result.stdout.splitlines():
        if line.startswith("__SEP__"):
            if current_uid and current_json:
                try:
                    meta = json.loads("\n".join(current_json))
                    if meta.get("type") == "CollectionType":
                        folders[meta["visibleName"]] = current_uid
                except Exception:
                    pass
            current_uid = Path(line[7:]).stem
            current_json = []
        else:
            current_json.append(line)
    if current_uid and current_json:
        try:
            meta = json.loads("\n".join(current_json))
            if meta.get("type") == "CollectionType":
                folders[meta["visibleName"]] = current_uid
        except Exception:
            pass
    return folders


def resolve_parent(folder: str | None) -> str:
    if not folder:
        return ""
    folders = list_folders()
    if folder in folders:
        return folders[folder]
    raise ValueError(f"Folder {folder!r} not found. Available: {list(folders.keys())}")


def push(title: str, pdf_bytes: bytes, parent: str = "") -> str:
    doc_id = str(uuid.uuid4())
    ts = str(int(time.time() * 1000))

    metadata = {
        "createdTime": ts,
        "lastModified": ts,
        "lastOpened": ts,
        "lastOpenedPage": 0,
        "new": True,
        "parent": parent,
        "pinned": False,
        "source": "",
        "type": "DocumentType",
        "visibleName": title,
    }
    content = {
        "coverPageNumber": 0,
        "documentMetadata": {},
        "extraMetadata": {},
        "fileType": "pdf",
        "fontName": "",
        "formatVersion": 1,
        "lineHeight": -1,
        "margins": 125,
        "orientation": "portrait",
        "pageCount": 1,
        "pageTags": [],
        "tags": [],
        "textAlignment": "left",
        "textScale": 1,
        "zoomMode": "bestFit",
    }

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / f"{doc_id}.pdf").write_bytes(pdf_bytes)
        (d / f"{doc_id}.metadata").write_text(json.dumps(metadata, indent=4))
        (d / f"{doc_id}.content").write_text(json.dumps(content, indent=4))

        def scp(src: str):
            subprocess.run([
                "sshpass", "-p", REMARKABLE_PASS,
                "scp", "-o", "StrictHostKeyChecking=no",
                src, f"{REMARKABLE_USER}@{REMARKABLE_HOST}:{XOCHITL_DIR}/",
            ], check=True)

        for f in d.iterdir():
            scp(str(f))

    subprocess.run([
        "sshpass", "-p", REMARKABLE_PASS,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{REMARKABLE_USER}@{REMARKABLE_HOST}",
        "systemctl restart xochitl",
    ], check=True)

    return doc_id


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default=None, help="Destination folder name (default: root)")
    parser.add_argument("--list-folders", action="store_true")
    args = parser.parse_args()

    if args.list_folders:
        for name, uid in list_folders().items():
            print(f"  {name}: {uid}")
        raise SystemExit(0)

    parent_id = resolve_parent(args.folder)

    title = "Voice Agent: Tool Calling Requirements"
    body = """
## Overview

Give the Turmeric voice agent the ability to dispatch work and receive results
without requiring a screen. Call in, talk through requirements, have them
executed and delivered back.

## Architecture

- Voice I/O: obscura (Twilio → STT → LLM → TTS → Twilio)
- Tool execution: sandboxed on lens via SSH dispatch
- Agentic loop: opencode / Claude Code on lens
- Result delivery: push PDF to reMarkable over SSH

## MVP Scope

- Synchronous dispatch for quick tools (bd read/create, shell one-liners)
- Async dispatch for long-running jobs with result delivery to reMarkable
- Tools: bd, shell exec on lens, bd remember from voice

## Open Questions

- Callback path for async results (reMarkable push confirmed viable)
- Whether Aperture or lens is the right execution target for impl tasks
- barge-in and echo cancellation for the voice layer
"""
    print(f"Building PDF...")
    pdf = make_pdf(title, body)
    print(f"Pushing to reMarkable (folder: {args.folder or 'root'})...")
    doc_id = push(title, pdf, parent=parent_id)
    print(f"Done. Document ID: {doc_id}")
    print("Done. Check your reMarkable in a few seconds.")
