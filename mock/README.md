# Mock YouTube Chat

This is an external dummy implementation of YouTube's `liveChatMessages.streamList` gRPC API, with an HTTP endpoint for mock channel publishing.
The stream app keeps its normal transports: incoming messages use the gRPC reader, while operator channel posts use the configured HTTP endpoint.

## Run

```bash
python3 -m venv .venv-mock
. .venv-mock/bin/activate
pip install -r mock/requirements.txt
python mock/mock_youtube_chat.py
```

The defaults are:

```text
gRPC: 127.0.0.1:18082
UI:   http://127.0.0.1:18083/
```

The mock is intentionally external to Compose. This preserves the real network
boundary: the app still consumes `StreamList` through gRPC rather than importing
mock state or switching to an in-process reader.

## Point the stream app at it

In local `.env`:

```env
YOUTUBE_CHAT_TRANSPORT=grpc
YOUTUBE_GRPC_TARGET=host.containers.internal:18082
YOUTUBE_GRPC_INSECURE=true
YOUTUBE_LIVE_CHAT_ID=mock-live-chat
YOUTUBE_PUBLISH_URL=http://host.containers.internal:18083/api/channel-messages
YOUTUBE_IGNORE_OWNER=true
YOUTUBE_ALLOWED_AUTHORS=Mock Viewer
```

Then restart the stream app container.

For production, remove the mock values, use
`YOUTUBE_GRPC_TARGET=youtube.googleapis.com:443`, set
`YOUTUBE_GRPC_INSECURE=false`, and leave `YOUTUBE_PUBLISH_URL` empty. Publishing
will then use the dedicated Google OAuth account from `.env`.

## Send messages

Open the mock UI:

```text
http://127.0.0.1:18083/
```

It keeps a YouTube-like chat history and uses `Mock Viewer` as the default username. When `YOUTUBE_ALLOWED_AUTHORS` is set as above, other mock usernames remain visible in chat but are not sent to AI replies or audience commands.
Templates and custom messages published from the operator console appear as `Night Shift Channel` posts in the same history. Production leaves `YOUTUBE_PUBLISH_URL` empty and continues publishing through YouTube OAuth.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Browser chat client |
| `GET` | `/api/status` | Message count and gRPC availability |
| `GET` | `/api/messages` | Current retained history |
| `POST` | `/api/messages` | Add a viewer-authored message |
| `POST` | `/api/channel-messages` | Add a channel-owned operator post |

Viewer request example:

```bash
curl -H 'Content-Type: application/json' \
	-d '{"author":"Mock Viewer","text":"switch to 1m"}' \
	http://127.0.0.1:18083/api/messages
```

Channel publish requests accept `{"text":"..."}`. The stream app uses that
endpoint through `YOUTUBE_PUBLISH_URL`; operators normally publish from the
console rather than calling it directly.

## Message behavior

- Viewer and channel messages are delivered over the same gRPC stream.
- Channel messages set `is_chat_owner=true`, matching the production field the
	bridge uses for owner suppression.
- The app remembers the ID returned by channel publishing to prevent feedback
	even when owner handling is deliberately enabled.
- History retains the newest 200 entries. Continuation offsets remain absolute
	after trimming, so long-running clients continue receiving new messages.
- A first connection receives retained history as backlog; the app records the
	continuation without speaking or executing that backlog.

## Test

```bash
uv run --with grpcio==1.74.0 --with grpcio-tools==1.74.0 \
	python -m unittest mock/test_mock_youtube_chat.py
```
