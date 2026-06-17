from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path

LOG = Path('/Users/lucianchiu/line_webhook_render_bot.log')
WEBHOOK_PATH = os.environ.get('LINE_WEBHOOK_PATH', '/line/webhook')
PORT = int(os.environ.get('PORT', '10000'))

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
        record = {
            'path': self.path,
            'event_count': len(events),
            'has_signature': bool(self.headers.get('X-Line-Signature')),
            'body': data,
        }
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        with LOG.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
        self._send(200, json.dumps({'ok': True, 'event_count': len(events)}).encode('utf-8'), content_type='application/json; charset=utf-8')

if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'listening on 0.0.0.0:{PORT}', flush=True)
    server.serve_forever()
