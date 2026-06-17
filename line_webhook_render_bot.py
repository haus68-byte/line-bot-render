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


def line_api(path: str, payload: dict):
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


def reply_message(reply_token: str, text: str):
    return line_api('/message/reply', {
        'replyToken': reply_token,
        'messages': [{'type': 'text', 'text': text}],
    })


def push_message(target_id: str, text: str):
    return line_api('/message/push', {
        'to': target_id,
        'messages': [{'type': 'text', 'text': text}],
    })


def get_target_id(source: dict):
    source_type = source.get('type')
    if source_type == 'user':
        return source.get('userId'), 'user'
    if source_type == 'group':
        return source.get('groupId'), 'group'
    if source_type == 'room':
        return source.get('roomId'), 'room'
    return None, source_type


def compact_job(job: dict) -> dict:
    return {
        'id': job['id'],
        'mode': job['mode'],
        'text': job['text'],
        'target_id': job['target_id'],
        'target_type': job['target_type'],
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
            ok, detail = push_message(job['target_id'], text)
            job['status'] = 'done' if ok else 'push_failed'
            job['result'] = text
            job['push_detail'] = detail
            self.log_json({'bridge_reply': job_id, 'ok': ok, 'detail': detail, 'target_type': job.get('target_type')})
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
            stripped = text.strip()
            reply_token = event.get('replyToken')
            source = event.get('source', {}) if isinstance(event.get('source'), dict) else {}
            target_id, target_type = get_target_id(source)
            # /trjp can be used as either a prefix or suffix:
            #   /trjp 你好
            #   你好 /trjp
            # One-to-one: every text message goes to Hermes; /trjp switches to translation.
            # Group/room: only /trjp messages trigger Hermes so the bot does not answer every group chat.
            is_trjp = stripped == '/trjp' or stripped.startswith('/trjp ') or stripped.endswith(' /trjp') or stripped.endswith('\n/trjp')

            if not is_trjp and target_type != 'user':
                results.append({'ignored': 'non_trjp_group_or_room_message', 'target_type': target_type})
                continue

            if not target_id:
                if reply_token:
                    results.append({'reply': reply_message(reply_token, '無法取得回覆目標，暫時不能處理。')})
                continue

            if is_trjp:
                if stripped == '/trjp':
                    source_text = ''
                elif stripped.startswith('/trjp '):
                    source_text = stripped[6:].strip()
                elif stripped.endswith(' /trjp'):
                    source_text = stripped[:-6].strip()
                else:
                    source_text = stripped[:-5].strip()
                if not source_text:
                    if reply_token:
                        results.append({'reply': reply_message(reply_token, '請輸入要翻譯的文字，例如：/trjp 你好 或 你好 /trjp')})
                    continue
                mode = 'translate'
                job_text = source_text
                ack = f'翻譯中…\n原文：{source_text}'
            else:
                mode = 'chat'
                job_text = text
                ack = 'Hermes 思考中…'

            job_id = str(uuid.uuid4())
            JOBS[job_id] = {
                'id': job_id,
                'mode': mode,
                'text': job_text,
                'target_id': target_id,
                'target_type': target_type,
                'status': 'pending',
                'created_at': time.time(),
                'attempts': 0,
            }
            if reply_token:
                ok, detail = reply_message(reply_token, ack)
                results.append({'queued': job_id, 'mode': mode, 'target_type': target_type, 'reply_ok': ok, 'detail': detail})
            else:
                results.append({'queued': job_id, 'mode': mode, 'target_type': target_type, 'reply_ok': False, 'detail': 'missing replyToken'})

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
