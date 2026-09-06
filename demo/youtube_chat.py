#!/usr/bin/env python3
"""Small YouTube Live Chat bridge using only the Python standard library.

Secrets are read from environment variables and are never returned in status.
The bridge deliberately separates inbound viewer events from outbound channel
messages: the former go to a callback, while the latter are only sent when an
operator/controller explicitly calls ``publish``.
"""
from collections import deque
import json
import os
import queue
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


API_ROOT = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHAT_TRANSPORT = os.environ.get("YOUTUBE_CHAT_TRANSPORT", "grpc").lower()
GRPC_TARGET = os.environ.get("YOUTUBE_GRPC_TARGET", "youtube.googleapis.com:443")
GRPC_INSECURE = os.environ.get("YOUTUBE_GRPC_INSECURE", "false").lower() in ("1", "true", "yes")
PUBLISH_URL = os.environ.get("YOUTUBE_PUBLISH_URL", "").strip()


class YouTubeError(RuntimeError):
    pass


class OAuthTokens:
    def __init__(self, client_id, client_secret, refresh_token,
                 access_token="", expires_at=0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.expires_at = float(expires_at or 0)
        if self.expires_at > 10_000_000_000:  # token.json uses milliseconds
            self.expires_at /= 1000
        self._lock = threading.Lock()

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def get(self, force_refresh=False):
        with self._lock:
            if (not force_refresh and self.access_token and
                    self.expires_at > time.time() + 60):
                return self.access_token
            if not self.configured:
                raise YouTubeError("YouTube OAuth is not configured")
            body = urllib.parse.urlencode({
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }).encode()
            req = urllib.request.Request(
                TOKEN_URL, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as exc:
                detail = exc.read(512).decode("utf-8", "replace")
                raise YouTubeError(f"OAuth refresh failed ({exc.code}): {detail}") from exc
            self.access_token = result["access_token"]
            self.expires_at = time.time() + int(result.get("expires_in", 3600))
            return self.access_token


class YouTubeClient:
    def __init__(self, tokens, api_root=API_ROOT, publish_url=""):
        self.tokens = tokens
        self.api_root = api_root.rstrip("/")
        self.publish_url = publish_url.strip()

    @property
    def can_publish(self):
        return bool(self.publish_url or self.tokens.configured)

    def _request(self, method, resource, params=None, payload=None):
        query = urllib.parse.urlencode(params or {})
        url = f"{self.api_root}/{resource}" + (f"?{query}" if query else "")
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(2):
            token = self.tokens.get(force_refresh=attempt == 1)
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    continue
                detail = exc.read(1024).decode("utf-8", "replace")
                raise YouTubeError(f"YouTube API {exc.code}: {detail}") from exc
        raise YouTubeError("YouTube authentication failed")

    def active_live_chat_id(self):
        result = self._request("GET", "liveBroadcasts", {
            # `broadcastStatus` and `mine` are mutually exclusive filters.
            # An authorized liveBroadcasts.list call is already scoped to the
            # authenticated channel, so active is sufficient here.
            "part": "snippet", "broadcastStatus": "active",
            "maxResults": 5,
        })
        for item in result.get("items", []):
            chat_id = item.get("snippet", {}).get("liveChatId")
            if chat_id:
                return chat_id
        return None

    def messages(self, live_chat_id, page_token=None):
        params = {
            "liveChatId": live_chat_id,
            "part": "id,snippet,authorDetails",
            "maxResults": 200,
            "profileImageSize": 88,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._request("GET", "liveChat/messages", params)

    def send(self, live_chat_id, text):
        if self.publish_url:
            request = urllib.request.Request(
                self.publish_url, data=json.dumps({"text": text}).encode(),
                method="POST", headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                })
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as exc:
                detail = exc.read(512).decode("utf-8", "replace")
                raise YouTubeError(
                    f"mock publish failed ({exc.code}): {detail}") from exc
            except urllib.error.URLError as exc:
                raise YouTubeError("mock publish endpoint is unavailable") from exc
            return result.get("message", result)
        return self._request("POST", "liveChat/messages", {"part": "snippet"}, {
            "snippet": {
                "liveChatId": live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": text},
            }
        })


class YouTubeBridge:
    """Poll incoming chat and serialize explicit outbound channel messages."""
    def __init__(self, client, on_message, live_chat_id="", ignore_owner=True,
                 transport=None, allowed_channel_ids=None, allowed_authors=None):
        self.client = client
        self.on_message = on_message
        self.live_chat_id = live_chat_id
        self._fixed_live_chat_id = bool(live_chat_id)
        self.ignore_owner = ignore_owner
        self.allowed_channel_ids = {
            str(value).strip() for value in (allowed_channel_ids or [])
            if str(value).strip()
        }
        self.allowed_authors = {
            str(value).strip().casefold() for value in (allowed_authors or [])
            if str(value).strip()
        }
        self.transport = (transport or CHAT_TRANSPORT).lower()
        if self.transport not in ("grpc", "rest"):
            raise ValueError("YouTube chat transport must be grpc or rest")
        self._stop = threading.Event()
        self._outbound = queue.Queue(maxsize=100)
        self._seen = set()
        self._seen_order = deque(maxlen=2000)
        self._seen_lock = threading.Lock()
        self._lock = threading.Lock()
        self._status = {
            "configured": self._inbound_configured(),
            "publish_configured": self._publishing_configured(),
            "publish_target": "mock" if getattr(client, "publish_url", "") else "youtube",
            "connected": False,
            "live_chat_id": bool(live_chat_id),
            "last_error": None,
            "received": 0,
            "spoken": 0,
            "published": 0,
            "outbound_queued": 0,
            "last_message_at": None,
            "transport": self.transport,
            "reconnects": 0,
            "continuations": 0,
            "identity_filter": bool(self.allowed_channel_ids or self.allowed_authors),
        }

    def status(self):
        with self._lock:
            return dict(self._status)

    def _set(self, **changes):
        with self._lock:
            self._status.update(changes)

    def mark_spoken(self):
        with self._lock:
            self._status["spoken"] += 1

    def _inbound_configured(self):
        return bool(self.client.tokens.configured or (
            self.transport == "grpc" and GRPC_INSECURE and self.live_chat_id))

    def _publishing_configured(self):
        return getattr(self.client, "can_publish", self.client.tokens.configured)

    def start(self):
        inbound = self._inbound_configured()
        outbound = self._publishing_configured()
        if not inbound and not outbound:
            return False
        if inbound:
            threading.Thread(target=self._poll_loop, daemon=True,
                             name="youtube-inbound").start()
        if outbound:
            threading.Thread(target=self._publish_loop, daemon=True,
                             name="youtube-outbound").start()
        return True

    def publish(self, text, category="manual", source="api"):
        if not self._publishing_configured():
            raise YouTubeError("YouTube publishing is not configured")
        text = " ".join(str(text).split()).strip()
        if not text:
            raise ValueError("message text is required")
        if len(text) > 200:
            raise ValueError("YouTube live-chat messages are limited to 200 characters")
        item = {"text": text, "category": str(category)[:24],
                "source": str(source)[:32], "queued_at": int(time.time())}
        try:
            self._outbound.put_nowait(item)
        except queue.Full as exc:
            raise YouTubeError("outbound YouTube queue is full") from exc
        self._set(outbound_queued=self._outbound.qsize())
        return item

    def _remember(self, message_id):
        with self._seen_lock:
            if not message_id or message_id in self._seen:
                return False
            if len(self._seen_order) == self._seen_order.maxlen:
                self._seen.discard(self._seen_order[0])
            self._seen.add(message_id)
            self._seen_order.append(message_id)
            return True

    def _identity_allowed(self, channel_id, author):
        if not self.allowed_channel_ids and not self.allowed_authors:
            return True
        return (str(channel_id or "").strip() in self.allowed_channel_ids or
                str(author or "").strip().casefold() in self.allowed_authors)

    def _normalize(self, item):
        snippet = item.get("snippet", {})
        author = item.get("authorDetails", {})
        event_type = snippet.get("type")
        if event_type == "textMessageEvent":
            text = (snippet.get("displayMessage") or
                    snippet.get("textMessageDetails", {}).get("messageText") or "").strip()
        elif event_type == "superChatEvent":
            details = snippet.get("superChatDetails", {})
            text = (details.get("userComment") or snippet.get("displayMessage") or "").strip()
        else:
            return None
        if (not text or (self.ignore_owner and author.get("isChatOwner")) or
            not self._identity_allowed(author.get("channelId"),
                           author.get("displayName"))):
            return None
        message = {
            "id": item.get("id", ""),
            "author": (author.get("displayName") or "YouTube viewer")[:80],
            "channel_id": author.get("channelId", ""),
            "text": text[:500],
            "published_at": snippet.get("publishedAt"),
            "is_owner": bool(author.get("isChatOwner")),
            "is_moderator": bool(author.get("isChatModerator")),
            "is_sponsor": bool(author.get("isChatSponsor")),
            "event_type": event_type,
            "platform": "youtube",
        }
        if event_type == "superChatEvent":
            details = snippet.get("superChatDetails", {})
            message["purchase_amount"] = details.get("amountDisplayString")
            message["amount_micros"] = int(details.get("amountMicros") or 0)
            message["tier"] = details.get("tier")
        return message

    def _normalize_grpc(self, item):
        """Convert the documented protobuf response to the internal shape."""
        snippet = item.snippet
        author = item.author_details
        event_types = {
            1: "textMessageEvent",
            15: "superChatEvent",
            16: "superStickerEvent",
            7: "newSponsorEvent",
            17: "memberMilestoneChatEvent",
            18: "membershipGiftingEvent",
            19: "giftMembershipReceivedEvent",
        }
        event_type = event_types.get(snippet.type)
        if event_type == "textMessageEvent":
            text = (snippet.text_message_details.message_text or
                    snippet.display_message).strip()
        elif event_type == "superChatEvent":
            text = (snippet.super_chat_details.user_comment or
                    snippet.display_message).strip()
        else:
            return None
        if (not text or (self.ignore_owner and author.is_chat_owner) or
            not self._identity_allowed(author.channel_id,
                           author.display_name)):
            return None
        message = {
            "id": item.id,
            "author": (author.display_name or "YouTube viewer")[:80],
            "channel_id": author.channel_id,
            "text": text[:500],
            "published_at": snippet.published_at,
            "is_owner": bool(author.is_chat_owner),
            "is_moderator": bool(author.is_chat_moderator),
            "is_sponsor": bool(author.is_chat_sponsor),
            "event_type": event_type,
            "platform": "youtube",
        }
        if event_type == "superChatEvent":
            details = snippet.super_chat_details
            message.update({
                "purchase_amount": details.amount_display_string,
                "amount_micros": int(details.amount_micros),
                "tier": int(details.tier),
            })
        return message

    def _ensure_chat(self):
        if self.live_chat_id:
            return True
        self.live_chat_id = self.client.active_live_chat_id()
        self._set(live_chat_id=bool(self.live_chat_id))
        return bool(self.live_chat_id)

    def _poll_loop(self):
        if self.transport == "grpc":
            return self._grpc_stream_loop()
        return self._rest_poll_loop()

    def _rest_poll_loop(self):
        page_token = None
        bootstrapped = False
        while not self._stop.is_set():
            try:
                if not self._ensure_chat():
                    self._set(connected=False, last_error="No active YouTube live chat")
                    self._stop.wait(20)
                    continue
                result = self.client.messages(self.live_chat_id, page_token)
                page_token = result.get("nextPageToken")
                self._set(connected=True, last_error=None)
                for item in result.get("items", []):
                    message_id = item.get("id", "")
                    is_new = self._remember(message_id)
                    # The first response is recent history. Record it without
                    # speaking so a restart cannot trigger a reply storm.
                    if not is_new or not bootstrapped:
                        continue
                    message = self._normalize(item)
                    if not message:
                        continue
                    self.on_message(message)
                    with self._lock:
                        self._status["received"] += 1
                        self._status["last_message_at"] = int(time.time())
                bootstrapped = True
                wait = max(1, min(30, int(result.get("pollingIntervalMillis", 5000)) / 1000))
                self._stop.wait(wait)
            except Exception as exc:
                self._set(connected=False, last_error=str(exc)[:300])
                if not self._fixed_live_chat_id:
                    self.live_chat_id = ""
                    page_token = None
                    bootstrapped = False
                    self._set(live_chat_id=False)
                self._stop.wait(10)

    def _grpc_stream_loop(self):
        """Consume one long-lived official stream and resume after failures."""
        try:
            import grpc
            import stream_list_pb2
            import stream_list_pb2_grpc
        except ImportError as exc:
            self._set(connected=False, last_error=f"gRPC unavailable: {exc}")
            return

        target = GRPC_TARGET
        insecure = GRPC_INSECURE
        if target.startswith("http://"):
            target = target.removeprefix("http://")
            insecure = True
        elif target.startswith("https://"):
            target = target.removeprefix("https://")
        channel_factory = grpc.insecure_channel if insecure else grpc.secure_channel
        credentials = () if insecure else (grpc.ssl_channel_credentials(),)
        channel = channel_factory(target, *credentials, options=(
            ("grpc.keepalive_time_ms", 60000),
            ("grpc.keepalive_timeout_ms", 20000),
            ("grpc.keepalive_permit_without_calls", 0),
        ))
        stub = stream_list_pb2_grpc.V3DataLiveChatMessageServiceStub(channel)
        page_token = ""
        bootstrapped = False
        backoff = 1.0
        while not self._stop.is_set():
            try:
                if not self._ensure_chat():
                    self._set(connected=False, last_error="No active YouTube live chat")
                    self._stop.wait(30)
                    continue
                metadata = ()
                if not insecure:
                    token = self.client.tokens.get()
                    metadata = (("authorization", f"Bearer {token}"),)
                request = stream_list_pb2.LiveChatMessageListRequest(
                    live_chat_id=self.live_chat_id,
                    profile_image_size=88,
                    page_token=page_token,
                    part=["id", "snippet", "authorDetails"],
                )
                responses = stub.StreamList(
                    request, metadata=metadata,
                    wait_for_ready=True)
                got_response = False
                for response in responses:
                    got_response = True
                    if self._stop.is_set():
                        responses.cancel()
                        break
                    self._set(connected=True, last_error=None)
                    backoff = 1.0
                    if response.next_page_token:
                        page_token = response.next_page_token
                    for item in response.items:
                        is_new = self._remember(item.id)
                        if not is_new or not bootstrapped:
                            continue
                        message = self._normalize_grpc(item)
                        if not message:
                            continue
                        self.on_message(message)
                        with self._lock:
                            self._status["received"] += 1
                            self._status["last_message_at"] = int(time.time())
                    bootstrapped = True
                    if response.offline_at:
                        raise YouTubeError("YouTube live chat ended")
                if self._stop.is_set():
                    break
                # A clean end with a token is normal protocol pagination, not
                # a network failure. Continue immediately on the same channel,
                # as in Google's reference client.
                if got_response and page_token:
                    self._set(connected=True,
                              continuations=self.status()["continuations"] + 1)
                    continue
                raise YouTubeError("YouTube gRPC stream closed")
            except grpc.RpcError as exc:
                code = exc.code()
                detail = exc.details() or str(exc)
                self._set(connected=False,
                          last_error=f"YouTube gRPC {code.name}: {detail}"[:300],
                          reconnects=self.status()["reconnects"] + 1)
                if code == grpc.StatusCode.UNAUTHENTICATED and self.client.tokens.configured:
                    try:
                        self.client.tokens.get(force_refresh=True)
                    except Exception:
                        pass
                if code in (grpc.StatusCode.NOT_FOUND,
                            grpc.StatusCode.FAILED_PRECONDITION):
                    if not self._fixed_live_chat_id:
                        self.live_chat_id = ""
                        page_token = ""
                        bootstrapped = False
                        self._set(live_chat_id=False)
                if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                    backoff = max(backoff, 60.0)
            except Exception as exc:
                self._set(connected=False, last_error=str(exc)[:300],
                          reconnects=self.status()["reconnects"] + 1)
                if "ended" in str(exc).lower() and not self._fixed_live_chat_id:
                    self.live_chat_id = ""
                    page_token = ""
                    bootstrapped = False
                    self._set(live_chat_id=False)
            delay = min(300.0, backoff) + random.uniform(0, min(3.0, backoff / 4))
            self._stop.wait(delay)
            backoff = min(300.0, backoff * 2)
        channel.close()

    def _publish_loop(self):
        while not self._stop.is_set():
            try:
                item = self._outbound.get(timeout=1)
            except queue.Empty:
                continue
            try:
                while not self._stop.is_set():
                    try:
                        if not self._ensure_chat():
                            raise YouTubeError("No active YouTube live chat")
                        sent = self.client.send(self.live_chat_id, item["text"])
                        # Suppress only the exact channel message we emitted;
                        # genuine owner-authored commands remain eligible.
                        self._remember(sent.get("id", ""))
                        with self._lock:
                            self._status["published"] += 1
                        break
                    except Exception as exc:
                        self._set(last_error=str(exc)[:300])
                        if not self._fixed_live_chat_id:
                            self.live_chat_id = ""
                            self._set(live_chat_id=False, connected=False)
                        self._stop.wait(10)
            finally:
                self._outbound.task_done()
                self._set(outbound_queued=self._outbound.qsize())


def bridge_from_env(on_message):
    transport = CHAT_TRANSPORT
    tokens = OAuthTokens(
        os.environ.get("YOUTUBE_CLIENT_ID", ""),
        os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
        os.environ.get("YOUTUBE_REFRESH_TOKEN", ""),
        os.environ.get("YOUTUBE_ACCESS_TOKEN", ""),
        os.environ.get("YOUTUBE_TOKEN_EXPIRY", "0"),
    )
    ignore_owner = os.environ.get("YOUTUBE_IGNORE_OWNER", "true").lower() not in ("0", "false", "no")
    allowed_channel_ids = os.environ.get("YOUTUBE_ALLOWED_CHANNEL_IDS", "").split(",")
    allowed_authors = os.environ.get("YOUTUBE_ALLOWED_AUTHORS", "").split(",")
    return YouTubeBridge(YouTubeClient(tokens, publish_url=PUBLISH_URL), on_message,
                         live_chat_id=os.environ.get("YOUTUBE_LIVE_CHAT_ID", ""),
                         ignore_owner=ignore_owner, transport=transport,
                         allowed_channel_ids=allowed_channel_ids,
                         allowed_authors=allowed_authors)
