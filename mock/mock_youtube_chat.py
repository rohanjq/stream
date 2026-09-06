#!/usr/bin/env python3
"""External mock for YouTube liveChatMessages.streamList.

The stream app still uses its normal gRPC reader. Point it at this server with:
  YOUTUBE_CHAT_TRANSPORT=grpc
  YOUTUBE_GRPC_TARGET=host.containers.internal:18082
  YOUTUBE_GRPC_INSECURE=true
  YOUTUBE_LIVE_CHAT_ID=mock-live-chat
"""
from concurrent import futures
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlparse

import grpc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO = os.path.join(ROOT, "demo", "stream_list.proto")
GENERATED = os.path.join(tempfile.gettempdir(), "nightshift_mock_youtube_pb")


def load_proto_modules():
    os.makedirs(GENERATED, exist_ok=True)
    out = os.path.join(GENERATED, "stream_list_pb2.py")
    if not os.path.isfile(out) or os.path.getmtime(out) < os.path.getmtime(PROTO):
        subprocess.check_call([
            sys.executable, "-m", "grpc_tools.protoc",
            f"-I{os.path.dirname(PROTO)}",
            f"--python_out={GENERATED}",
            f"--grpc_python_out={GENERATED}",
            PROTO,
        ])
    if GENERATED not in sys.path:
        sys.path.insert(0, GENERATED)
    import stream_list_pb2
    import stream_list_pb2_grpc
    return stream_list_pb2, stream_list_pb2_grpc


pb2, pb2_grpc = load_proto_modules()


class ChatState:
    def __init__(self):
        self._condition = threading.Condition()
        self._messages = []
        self._base_offset = 0

    def add(self, author, text, is_moderator=False, is_sponsor=False,
            event_type="textMessageEvent", amount_micros=0,
            purchase_amount="", tier=0, is_owner=False):
        author = str(author or "Mock Viewer")[:80]
        text = " ".join(str(text or "").split())[:500]
        if not text:
            raise ValueError("text is required")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._condition:
            message_id = f"mock-{uuid.uuid4().hex}"
            self._messages.append({
                "id": message_id,
                "author": author,
                "text": text,
                "published_at": now,
                "is_moderator": bool(is_moderator),
                "is_sponsor": bool(is_sponsor),
                "is_owner": bool(is_owner),
                "event_type": event_type,
                "amount_micros": int(amount_micros or 0),
                "purchase_amount": str(purchase_amount or ""),
                "tier": int(tier or 0),
            })
            overflow = max(0, len(self._messages) - 200)
            if overflow:
                del self._messages[:overflow]
                self._base_offset += overflow
            self._condition.notify_all()
            return self._messages[-1]

    def snapshot(self):
        with self._condition:
            return list(self._messages)

    def snapshot_with_offset(self):
        with self._condition:
            return list(self._messages), self._base_offset + len(self._messages)

    def wait_after(self, offset, timeout=30):
        deadline = time.time() + timeout
        with self._condition:
            offset = max(offset, self._base_offset)
            while self._base_offset + len(self._messages) <= offset:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            start = max(0, offset - self._base_offset)
            return (list(self._messages[start:]),
                    self._base_offset + len(self._messages))


STATE = ChatState()


def to_proto(message):
    kind = pb2.LiveChatMessageSnippet.TEXT_MESSAGE_EVENT
    if message["event_type"] == "superChatEvent":
        kind = pb2.LiveChatMessageSnippet.SUPER_CHAT_EVENT
    item = pb2.LiveChatMessage(id=message["id"])
    item.author_details.channel_id = "mock-channel"
    item.author_details.display_name = message["author"]
    item.author_details.is_chat_owner = message["is_owner"]
    item.author_details.is_chat_moderator = message["is_moderator"]
    item.author_details.is_chat_sponsor = message["is_sponsor"]
    item.snippet.type = kind
    item.snippet.published_at = message["published_at"]
    item.snippet.display_message = message["text"]
    item.snippet.text_message_details.message_text = message["text"]
    if kind == pb2.LiveChatMessageSnippet.SUPER_CHAT_EVENT:
        item.snippet.super_chat_details.user_comment = message["text"]
        item.snippet.super_chat_details.amount_micros = message["amount_micros"]
        item.snippet.super_chat_details.amount_display_string = message["purchase_amount"]
        item.snippet.super_chat_details.tier = message["tier"]
    return item


class LiveChatService(pb2_grpc.V3DataLiveChatMessageServiceServicer):
    def StreamList(self, request, context):
        try:
            offset = int(request.page_token or 0)
        except ValueError:
            offset = 0
        if offset == 0:
            backlog, offset = STATE.snapshot_with_offset()
            yield pb2.LiveChatMessageListResponse(
                next_page_token=str(offset),
                items=[to_proto(message) for message in backlog],
            )
        while context.is_active():
            messages, offset = STATE.wait_after(offset)
            yield pb2.LiveChatMessageListResponse(
                next_page_token=str(offset),
                items=[to_proto(message) for message in messages],
            )


PAGE = """<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Mock YouTube Chat</title>
<style>
:root{--bg:#0f0f0f;--panel:#181818;--line:#303030;--text:#f1f1f1;--muted:#aaa;--accent:#3ea6ff;--bubble:#242424;--ok:#8ae3a2;--bad:#ff7b88}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font:14px/1.45 Arial,Helvetica,sans-serif;display:grid;place-items:center;padding:20px}main{width:min(520px,100%);height:min(820px,calc(100vh - 40px));display:grid;grid-template-rows:auto 1fr auto;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}header{padding:16px;border-bottom:1px solid var(--line)}h1{font-size:20px;margin:0}.sub{color:var(--muted);font-size:13px;margin-top:4px}#log{overflow:auto;background:#0f0f0f;padding:8px 0}.msg{display:grid;grid-template-columns:36px 1fr;gap:10px;padding:9px 16px}.msg.owner{background:#182536}.avatar{width:36px;height:36px;border-radius:50%;background:#5f6368;display:grid;place-items:center;font-weight:800}.owner .avatar{background:var(--accent);color:#06131f}.name{font-weight:700}.owner .name{color:var(--accent)}.meta{color:var(--muted);font-size:12px;margin-left:7px}.body{color:#ddd;overflow-wrap:anywhere}.composer{border-top:1px solid var(--line);padding:14px 16px}label{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}input,textarea{width:100%;margin-top:7px;border:1px solid var(--line);background:#111;color:var(--text);border-radius:6px;padding:11px 12px;font:15px/1.35 Arial,Helvetica,sans-serif}textarea{min-height:74px;resize:vertical}.actions{display:flex;gap:10px;align-items:center;margin-top:12px}button{background:var(--accent);color:#06131f;border:0;border-radius:18px;padding:10px 16px;font-weight:700;cursor:pointer}.status{color:var(--muted);font-size:12px}.status.ok{color:var(--ok)}.status.bad{color:var(--bad)}@media(max-width:640px){body{padding:0}main{height:100vh;border:0;border-radius:0}}
</style></head><body><main><header><h1>Mock YouTube Chat</h1><div class=\"sub\">External gRPC dummy chat. The stream reader only sees liveChatMessages.streamList.</div></header><section id=\"log\"></section><section class=\"composer\"><label>Username<input id=\"author\" value=\"Mock Viewer\" autocomplete=\"name\"></label><label style=\"margin-top:10px\">Message<textarea id=\"text\" autofocus placeholder=\"Type a chat message...\"></textarea></label><div class=\"actions\"><button id=\"send\">Send</button><span id=\"status\" class=\"status\">Ready</span></div></section></main><script>
const author=document.getElementById('author'), text=document.getElementById('text'), log=document.getElementById('log'), statusEl=document.getElementById('status');
author.value=localStorage.getItem('mock.youtube.author')||author.value;
let seen=new Set();
function setStatus(t,c=''){statusEl.className='status '+c;statusEl.textContent=t}
function row(m){if(seen.has(m.id))return;seen.add(m.id);const el=document.createElement('div');el.className='msg'+(m.is_owner?' owner':'');el.innerHTML='<div class=\"avatar\"></div><div><span class=\"name\"></span><span class=\"meta\"></span><div class=\"body\"></div></div>';el.querySelector('.avatar').textContent=(m.author||'?').slice(0,1).toUpperCase();el.querySelector('.name').textContent=m.author;el.querySelector('.meta').textContent=(m.is_owner?'CHANNEL · ':'')+(m.published_at||'');el.querySelector('.body').textContent=m.text;log.appendChild(el);log.scrollTop=log.scrollHeight}
async function refresh(){try{const d=await fetch('/api/messages').then(r=>r.json());(d.messages||[]).forEach(row)}catch(e){}setTimeout(refresh,800)}
async function send(){const body=text.value.trim();if(!body){setStatus('Message is required','bad');return}const name=(author.value.trim()||'Mock Viewer').slice(0,80);author.value=name;localStorage.setItem('mock.youtube.author',name);setStatus('Sending...');try{const r=await fetch('/api/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({author:name,text:body})});const d=await r.json();if(!r.ok)throw new Error(d.error||r.statusText);text.value='';row(d.message);setStatus('Sent','ok')}catch(e){setStatus(e.message,'bad')}text.focus()}
document.getElementById('send').onclick=send;text.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))send()});refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/messages":
            return self._json(200, {"messages": STATE.snapshot()})
        if path == "/api/status":
            return self._json(200, {"messages": len(STATE.snapshot()), "grpc": True})
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/messages", "/api/channel-messages"):
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(size) or b"{}")
            message = STATE.add(
                "Night Shift Channel" if path == "/api/channel-messages"
                else payload.get("author") or "Mock Viewer",
                payload.get("text") or "",
                is_moderator=payload.get("is_moderator", False),
                is_sponsor=payload.get("is_sponsor", False),
                event_type=payload.get("event_type") or "textMessageEvent",
                amount_micros=payload.get("amount_micros") or 0,
                purchase_amount=payload.get("purchase_amount") or "",
                tier=payload.get("tier") or 0,
                is_owner=path == "/api/channel-messages",
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        self._json(202, {"accepted": True, "message": message})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grpc", default="127.0.0.1:18082")
    parser.add_argument("--http", default="127.0.0.1:18083")
    args = parser.parse_args()

    grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb2_grpc.add_V3DataLiveChatMessageServiceServicer_to_server(
        LiveChatService(), grpc_server)
    grpc_server.add_insecure_port(args.grpc)
    grpc_server.start()

    host, port = args.http.rsplit(":", 1)
    print(f"mock youtube grpc: {args.grpc}", flush=True)
    print(f"mock youtube ui:   http://{args.http}/", flush=True)
    try:
        ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
    finally:
        grpc_server.stop(0)


if __name__ == "__main__":
    main()
