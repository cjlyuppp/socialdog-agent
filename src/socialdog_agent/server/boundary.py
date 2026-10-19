"""Bound request bodies before JSON parsing, including chunked transfers."""
from starlette.responses import JSONResponse


def request_body_limit(path):
    return 2_000_000 if path == '/api/admin/question-bank/draft' else 65536


class RequestBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] in ('GET', 'HEAD', 'OPTIONS'):
            return await self.app(scope, receive, send)
        limit = request_body_limit(scope['path'])
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > limit:
                response = JSONResponse({'error': '请求内容过大。'}, status_code=413)
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)

        async def bounded_receive():
            nonlocal body
            if body is not None:
                data, body = body, None
                return {'type': 'http.request', 'body': data, 'more_body': False}
            return await receive()

        await self.app(scope, bounded_receive, send)
