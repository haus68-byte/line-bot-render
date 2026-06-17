from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time
import uuid
from urllib import request, error, parse

WEBHOOK_PATH = os.environ.get('LINE_WEBHOOK_PATH', '/line/webhook')
PORT = int(os.environ.get('PORT', '10000'))
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN', '')
BRIDGE_SECRET = os.environ.get('BRIDGE_SECRET', '')

JOBS = {}
LEASE_SECONDS = 180


def line_api(path: str, payload: dict) -> tuple[bool, str]:
    if not CHANNEL_ACCESS_TOKEN:
        return False, 'CHANNEL_ACCESS_TOKEN not set'
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = request.Request(
        f'https://api.line.me/v2/bot{path}',
        data=data,
        headers={
            'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}',
            'Content-Type': 'application/json; charset=utf-8',
        },
        method='POST',
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300, f'LINE status {resp.status}'
    except error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore') if e.fp else ''
        return False, f'LINE HTTPError {e.code} {body}'
    except Exception as e:
        return False, f'LINE error {e}'


def reply_message(reply_token: str, text: str) -> tuple[bool, str]:
    return line_api('/message/reply', {
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': text}],
    })


def push_message(user_id: str, text: str) -> tuple[bool, str]:
    return line_api('/message/push', {
        'to': user_id,
        'messages': [{'type': 'text', 'text': text}],
    })


def compact_job(job: dict) -> dict:
    return {
        'id': job['id'],
        'text': job['text'],
        'user_id': job['user_id'],
        'created_at': job['created_at'],
        'attempts': job['attempts'],
    }


class Handler(BaseHTTPRequestHandler):
    def log_json(self, record: dict):
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def _send(self, code=200, body=b'OK', content_type='text/plain; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self._send(code, body, 'application/json; charset=utf-8')

    def _auth_bridge(self) -> bool:
        if not BRIDGE_SECRET:
            return False
        header = self.headers.get('X-Bridge-Secret', '')
        return header == BRIDGE_SECRET

    def do_GET(self):
        parsed = parse.urlparse(self.path)
        if parsed.path in ('/', '/health'):
            self._send(200, b'OK')
            return
        if parsed.path == '/bridge/jobs':
            if not self._auth_bridge():
                self._json(401, {'ok': False, 'error': 'unauthorized'})
                return
            now = time.time()
            for job in JOBS.values():
                if job['status'] == 'pending' or (job['status'] == 'leased' and job.get('lease_until', 0) < now):
                    job['status'] = 'leased'
                    job['lease_until'] = now + LEASE_SECONDS
                    job['attempts'] += 1
                    self._json(200, {'ok': True, 'job': compact_job(job)})
                    return
            self._json(200, {'ok': True, 'job': None})
            return
        self._send(404, b'not found')

    def do_POST(self):
        parsed = parse.urlparse(self.path)
        length = int(self.headers.get('Content-Length', '0'))
        payload = self.rfile.read(length) if length else b''
        try:
            data = json.loads(payload.decode('utf-8') or '{}')
        except Exception:
            data = {'raw': payload.decode('utf-8', 'ignore')}

        if parsed.path == '/bridge/reply':
            if not self._auth_bridge():
                self._json(401, {'ok': False, 'error': 'unauthorized'})
                return
            job_id = data.get('id')
            text = data.get('text', '')
            job = JOBS.get(job_id)
            if not job:
                self._json(404, {'ok': False, 'error': 'job not found'})
                return
            ok, detail = push_message(job['user_id'], text)
            job['status'] = 'done' if ok else 'push_failed'
            job['result'] = text
            job['push_detail'] = detail
            self.log_json({'bridge_reply': job_id, 'ok': ok, 'detail': detail})
            self._json(200, {'ok': ok, 'detail': detail})
            return

        if parsed.path != WEBHOOK_PATH:
            self._send(404, b'not found')
            return

        events = data.get('events', []) if isinstance(data, dict) else []
        results = []
        for event in events:
            if not isinstance(event, dict) or event.get('type') != 'message':
                continue
            message = event.get('message', {})
            if not isinstance(message, dict) or message.get('type') != 'text':
                continue
            text = message.get('text', '')
            reply_token = event.get('replyToken')
            source = event.get('source', {}) if isinstance(event.get('source'), dict) else {}
            user_id = source.get('userId')

            if text.strip().endswith('/trjp'):
                source_text = text.strip()[:-5].strip()
                if not source_text:
                    if reply_token:
                        results.append({'reply': reply_message(reply_token, '請在文字後面加 /trjp，例如：你好 /trjp')})
                    continue
                if not user_id:
                    if reply_token:
                        results.append({'reply': reply_message(reply_token, '無法取得使用者 ID，暫時不能翻譯。')})
                    continue
                job_id = str(uuid.uuid4())
                JOBS[job_id] = {
                    'id': job_id,
                    'text': source_text,
                    'user_id': user_id,
                    'status': 'pending',
                    'created_at': time.time(),
                    'attempts': 0,
                }
                ack = f'翻譯中…\n原文：{source_text}'
                if reply_token:
                    ok, detail = reply_message(reply_token, ack)
                    results.append({'queued': job_id, 'reply_ok': ok, 'detail': detail})
                else:
                    results.append({'queued': job_id, 'reply_ok': False, 'detail': 'missing replyToken'})
            else:
                if reply_token:
                    ok, detail = reply_message(reply_token, f'收到：{text}')
                    results.append({'reply_ok': ok, 'detail': detail})

        self.log_json({
            'path': parsed.path,
            'event_count': len(events),
            'has_signature': bool(self.headers.get('X-Line-Signature')),
            'results': results,
        })
        self._json(200, {'ok': True, 'event_count': len(events), 'results': results})


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'listening on 0.0.0.0:{PORT}', flush=True)
    server.serve_forever()
