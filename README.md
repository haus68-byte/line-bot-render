# LINE Bot Render

Minimal LINE webhook app for Render.

## Environment variables
- `CHANNEL_ACCESS_TOKEN` required for reply API
- `LINE_WEBHOOK_PATH` defaults to `/line/webhook`
- `PORT` defaults to `10000`

## Behavior
- GET `/health` returns `OK`
- POST `/line/webhook` logs LINE events and replies `收到：<original text>` to text messages
