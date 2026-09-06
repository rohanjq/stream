# Mock YouTube Chat

This is an external dummy implementation of YouTube's `liveChatMessages.streamList` gRPC API.
The stream app does not switch to a mock reader; it keeps `YOUTUBE_CHAT_TRANSPORT=grpc` and only points `YOUTUBE_GRPC_TARGET` at this server.

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

## Point the stream app at it

In local `.env`:

```env
YOUTUBE_CHAT_TRANSPORT=grpc
YOUTUBE_GRPC_TARGET=host.containers.internal:18082
YOUTUBE_GRPC_INSECURE=true
YOUTUBE_LIVE_CHAT_ID=mock-live-chat
YOUTUBE_CLIENT_ID=mock-client
YOUTUBE_CLIENT_SECRET=mock-secret
YOUTUBE_REFRESH_TOKEN=mock-refresh
YOUTUBE_ACCESS_TOKEN=mock-access
YOUTUBE_TOKEN_EXPIRY=4102444800
```

Then restart the stream app container.

## Send messages

Open the mock UI:

```text
http://127.0.0.1:18083/
```

It keeps a YouTube-like chat history, uses `Mock Viewer` as the default username, and lets you change that username for all later messages.
