"""One DeepSeek credential per workspace, outside SQLite and public responses."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from fastapi import Depends

from socialdog_agent.llm import NoRedirect
from . import models as m
from .store import APIError, now


@dataclass(frozen=True)
class Credential:
    key: str = field(repr=False)
    updated_at: str | None = None


class DeepSeekCredentials:
    def __init__(self, workspace: Path, server_key: str):
        self.directory = Path(workspace) / '.socialdog-secrets'
        self.path = self.directory / 'deepseek.json'
        self.server_key = Credential(server_key.strip())
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return self.server_key
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(value['api_key'], str):
                    raise ValueError('Invalid credential record')
                return Credential(value['api_key'], value['updated_at'])
            except (OSError, ValueError, KeyError, TypeError):
                # A broken override must never silently reactivate an old server key.
                raise APIError(503, '模型服务配置无法读取，请由管理员重新保存 API Key。') from None

    def status(self):
        value = self.read()
        return {'provider': 'deepseek', 'configured': bool(value.key), 'updated_at': value.updated_at}

    def save(self, key: str, actor: str):
        with self.lock:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            fd, temporary = tempfile.mkstemp(prefix='.deepseek-', dir=self.directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump({'api_key': key, 'updated_at': now(), 'updated_by': actor}, file)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return self.status()


def validate_deepseek_key(key: str):
    """Validate authentication against the fixed official metadata endpoint."""
    request = Request('https://api.deepseek.com/models', headers={
        'Authorization': 'Bearer ' + key, 'Accept': 'application/json',
        'User-Agent': 'SocialDogResearchAgent/0.2'})
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('Response too large')
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('data'), list) or not result['data']:
            raise ValueError('Invalid model list')
    except HTTPError as error:
        messages = {401: 'DeepSeek API Key 无效，请检查后重试。', 402: 'DeepSeek 账户余额不足。',
                    403: 'DeepSeek 拒绝访问，请检查账户权限。', 429: 'DeepSeek 请求限流，请稍后重试。'}
        raise APIError(400, messages.get(error.code, 'DeepSeek 连接验证失败，请稍后重试。')) from None
    except (URLError, OSError):
        raise APIError(503, '无法连接 DeepSeek，请检查服务器网络后重试。') from None
    except (ValueError, TypeError, RecursionError):
        raise APIError(503, 'DeepSeek 返回了无法识别的结果，请稍后重试。') from None


def register_provider(app, credentials, admin):
    @app.get('/api/admin/model-settings', response_model=m.ModelServiceSettings)
    def model_settings(auth=Depends(admin)):
        return credentials.status()

    @app.put('/api/admin/model-settings', response_model=m.ModelServiceSettings)
    def save_key(body: m.DeepSeekKeyInput, auth=Depends(admin)):
        key = body.api_key.get_secret_value().strip()
        validate_deepseek_key(key)
        return credentials.save(key, auth[0]['id'])

    @app.post('/api/admin/model-settings/check', response_model=m.ModelServiceSettings)
    def check_key(auth=Depends(admin)):
        value = credentials.read()
        if not value.key:
            raise APIError(400, '请先填写并保存 API Key。')
        validate_deepseek_key(value.key)
        return credentials.status()

    @app.delete('/api/admin/model-settings', response_model=m.ModelServiceSettings)
    def disable(auth=Depends(admin)):
        # Persist an empty override so .env cannot unexpectedly enable it on restart.
        return credentials.save('', auth[0]['id'])
