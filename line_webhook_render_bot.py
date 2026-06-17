from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib import request, error

WEBHOOK_PATH = os.environ.get('LINE_WEBHOOK_PATH', '/line/webhook')
PORT = int(os.environ.get('PORT', '10000'))
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN', '')


def reply_line_message(reply_token: str, text: str) -> tuple[bool, str]:
    if not CHANNEL_ACCESS_TOKEN:
        return False, 'CHANNEL_ACCESS_TOKEN not set'
    payload = json.dumps({
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': text}],
    }).encode('utf-8')
    req = request.Request(
        'https://api.line.me/v2/bot/message/reply',
        data=payload,
        headers={
            'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}',
            'Content-Type': 'application/json; charset=utf-8',
        },
        method='POST',
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300, f'LINE reply status {resp.status}'
    except error.HTTPError as e:
        try:
            body = e.read().decode('utf-8', 'ignore')
        except Exception:
            body = ''
        return False, f'LINE reply HTTPError {e.code} {body}'
    except Exception as e:
        return False, f'LINE reply error {e}'


class Handler(BaseHTTPRequestHandler):
    def _send(self, code=200, body=b'OK', content_type='text/plain; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/health'):
            self._send(200, b'OK')
        else:
            self._send(404, b'not found')

    def do_POST(self):
        if self.path != WEBHOOK_PATH:
            self._send(404, b'not found')
            return

        length = int(self.headers.get('Content-Length', '0'))
        payload = self.rfile.read(length) if length else b''
        try:
            data = json.loads(payload.decode('utf-8') or '{}')
        except Exception:
            data = {'raw': payload.decode('utf-8', 'ignore')}

        events = data.get('events', []) if isinstance(data, dict) else []
        reply_results = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get('type') != 'message':
                continue
            message = event.get('message', {})
            if not isinstance(message, dict) or message.get('type') != 'text':
                continue
            reply_token = event.get('replyToken')
            text = message.get('text', '')
            if reply_token:
                ok, detail = reply_line_message(reply_token, f'收到：{text}')
                reply_results.append({'ok': ok, 'detail': detail})

        record = {
            'path': self.path,
            'event_count': len(events),
            'has_signature': bool(self.headers.get('X-Line-Signature')),
            'reply_results': reply_results,
            'body': data,
        }
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        self._send(200, json.dumps({'ok': True, 'event_count': len(events), 'replies': reply_results}, ensure_ascii=False).encode('utf-8'), content_type='application/json; charset=utf-8')


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'listening on 0.0.0.0:{PORT}', flush=True)
    server.serve_forever()
