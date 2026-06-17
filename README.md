# LINE Bot Render

Minimal LINE webhook app for Render.

## Environment variables
- `CHANNEL_ACCESS_TOKEN` required for reply API
- `LINE_WEBHOOK_PATH` defaults to `/line/webhook`
- `PORT` defaults to `10000`

## Behavior
- GET `/health` returns `OK`
- POST `/line/webhook` logs LINE events and queues Hermes jobs
- One-to-one chats: every text message is sent to Hermes chat mode
- One-to-one `/trjp` messages: sent to Traditional Chinese ⇄ Japanese translation mode
- Groups/rooms: only messages with `/trjp` trigger translation; other group messages are ignored
- `/trjp` works as either prefix or suffix: `/trjp 你好` or `你好 /trjp`

## Bridge environment variables
- `CHANNEL_ACCESS_TOKEN` required for LINE Reply/Push API
- `BRIDGE_SECRET` required for the Mac-local Hermes poller to pull jobs and send replies
- `LINE_WEBHOOK_PATH` defaults to `/line/webhook`
- `PORT` defaults to `10000`
