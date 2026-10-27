"""GitHub 图片与课程记录存储。所有上传确认后才返回成功。"""
import base64
import hashlib
import json
import logging
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)
DEFAULT_DATA_BRANCH = 'rs-data'
# 仅在一次上传内保留配置快照；结束后清除，不跨用户缓存令牌。
_OPERATION_CONTEXT = ContextVar('rs_storage_operation', default=None)


class StorageError(RuntimeError):
    """可直接显示给用户的存储错误，不包含令牌。"""


class StorageConfigurationError(StorageError):
    pass


def _configuration(token=None, repo=None):
    try:
        cfg = st.secrets['github_storage']
    except (KeyError, FileNotFoundError):
        cfg = {}
    token = token or cfg.get('token')
    repo = repo or cfg.get('repo')
    if not isinstance(token, str) or not token.strip():
        raise StorageConfigurationError(
            '当前运行实例未读取到 [github_storage] 中的 token。'
            '若云端配置已保存，请重新保存 Secrets 并重启应用后重试，无需重新创建令牌。'
            '令牌只填写在 Streamlit 云端 Secrets 中。'
        )
    if not isinstance(repo, str) or len(repo.strip().split('/')) != 2:
        raise StorageConfigurationError('github_storage.repo 必须填写“账号/仓库名”，例如 llllll129/rs。')
    repo = repo.strip()
    if any(not part or any(ch.isspace() for ch in part) for part in repo.split('/')) or ':' in repo or '..' in repo:
        raise StorageConfigurationError('github_storage.repo 格式无效，请填写“账号/仓库名”。')
    branch = cfg.get('data_branch', cfg.get('branch', ''))
    if not isinstance(branch, str):
        raise StorageConfigurationError('github_storage.branch 必须是分支名称字符串。')
    return token.strip(), repo, branch.strip()


def get_github_config():
    token, repo, _ = _configuration()
    return token, repo


def storage_status():
    """仅检查配置格式，不联网，不输出密钥。"""
    try:
        _, repo, branch = _configuration()
        return True, f'存储仓库：{repo}；数据与部署分支分开保存（实际数据分支可通过检查连接查看）'
    except StorageConfigurationError as exc:
        return False, str(exc)


def _request(method, repo, path='', *, token, allow_missing=False, raw=False, **kwargs):
    url = f'https://api.github.com/repos/{repo}' + (f'/{path}' if path else '')
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    try:
        response = requests.request(method, url, headers=headers, timeout=(10, 60), **kwargs)
    except requests.RequestException:
        raise StorageError('GitHub 连接中断或超时，保存结果尚不确定；请重试，系统会核查已上传文件。') from None
    if response.status_code == 404 and allow_missing:
        return None
    if not response.ok:
        reasons = {
            401: '令牌无效或已过期，请更新 Streamlit Secrets 中的 token。',
            403: '仓库权限、访问策略或 API 限额阻止了请求；请检查 Contents 读写权限和分支规则。',
            404: '仓库、分支或文件不存在，或令牌无权访问。',
            409: '记录已被修改或分支冲突，请刷新后重新操作。',
            422: '写入被拒绝，请检查分支规则、文件路径和仓库状态。',
            429: '请求过于频繁，请稍后重试。',
        }
        reason = reasons.get(response.status_code, 'GitHub 服务暂不可用，请稍后重试。')
        raise StorageError(f'GitHub HTTP {response.status_code}：{reason}')
    return response


@lru_cache(maxsize=8)
def _branch_for(token, repo, configured_branch):
    info = _request('GET', repo, token=token).json()
    # 旧配置若指向仓库默认部署分支，也迁往独立数据分支。
    branch = configured_branch or DEFAULT_DATA_BRANCH
    if branch == info['default_branch']:
        branch = DEFAULT_DATA_BRANCH
    # 先核实分支，避免把“分支不存在”的 404 当作“JSON 尚未创建”。
    _request('GET', repo, 'git/ref/heads/' + quote(branch, safe='/'), token=token)
    return branch


def _context(token=None, repo=None):
    active = _OPERATION_CONTEXT.get()
    if active is not None and token is None and repo is None:
        return active
    token, repo, configured = _configuration(token, repo)
    return token, repo, _branch_for(token, repo, configured)


def _path(path):
    normalized = str(path).replace(chr(92), '/').strip('/')
    if not normalized or '..' in PurePosixPath(normalized).parts or ':' in normalized:
        raise StorageError('存储路径必须是仓库内的相对路径。')
    return normalized


def _content(token, repo, branch, path, *, raw=False):
    return _request('GET', repo, 'contents/' + quote(_path(path), safe='/'),
                    token=token, params={'ref': branch}, allow_missing=True, raw=raw)


def _git_sha(data):
    return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def _raw_url(repo, branch, path):
    return f'https://raw.githubusercontent.com/{repo}/{quote(branch, safe="")}/{quote(path, safe="/")}'


def github_request(method, path, **kwargs):
    """保留原项目接口；新代码优先使用下方明确的读写函数。"""
    token, repo, branch = _context()
    if method.upper() == 'GET':
        kwargs.setdefault('params', {'ref': branch})
    return _request(method, repo, 'contents/' + quote(_path(path), safe='/'), token=token, **kwargs).json()


def upload_file(uploaded_file, folder='images', token=None, repo=None):
    token, repo, branch = _context(token, repo)
    # 同一内容使用稳定文件名，重复点击或网络重试不再生成大量重复文件。
    uploaded_file.seek(0)
    data = uploaded_file.read()
    if not data:
        raise StorageError('文件为空，请重新选择。')
    if len(data) > 25 * 1024 * 1024:
        raise StorageError('当前存储方案限制单文件 25 MiB；请压缩文件或另行配置大文件存储。')
    ext = Path(uploaded_file.name).suffix.lower()
    if ext and (len(ext) > 12 or not ext[1:].isalnum()):
        raise StorageError('文件扩展名无效。')
    path = f'{_path(folder)}/{hashlib.sha256(data).hexdigest()}{ext}'
    expected_sha = _git_sha(data)
    existing = _content(token, repo, branch, path)
    if existing is not None:
        if existing.json().get('sha') != expected_sha:
            raise StorageError('目标文件内容校验不一致，已停止覆盖。')
        return _raw_url(repo, branch, path)
    payload = {'message': f'Store {path}', 'content': base64.b64encode(data).decode(), 'branch': branch}
    try:
        result = _request('PUT', repo, 'contents/' + quote(path, safe='/'), token=token, json=payload).json()
        if result.get('content', {}).get('sha') != expected_sha:
            raise StorageError('上传响应校验失败，请重试核查。')
    except StorageError:
        # PUT 可能已成功但响应丢失，也可能是其他用户同时上传相同内容。
        check = _content(token, repo, branch, path)
        if check is None or check.json().get('sha') != expected_sha:
            raise
    return _raw_url(repo, branch, path)


def _snapshot_key(repo, branch, path):
    return '_storage_baseline:' + json.dumps([repo, branch, path], ensure_ascii=False)


def _json_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def _read_json(token, repo, branch, path):
    meta = _content(token, repo, branch, path)
    if meta is None:
        return None, None
    record = meta.json()
    try:
        if record.get('encoding') == 'base64':
            raw = base64.b64decode(record['content'])
        else:
            # GitHub 对较大的 JSON 不提供内嵌 content，需认证读取原始内容。
            result = _content(token, repo, branch, path, raw=True)
            if result is None:
                raise StorageError('读取期间课程文件发生变化，请刷新。')
            raw = result.content
        if _git_sha(raw) != record['sha']:
            raise StorageError('读取期间课程文件发生变化，请刷新后重试。')
        data = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, KeyError, UnicodeError):
        raise StorageError('远程课程 JSON 格式损坏或响应不完整，已停止读取和覆盖。') from None
    expected = dict if path == 'chapter_structure.json' else list
    if not isinstance(data, expected):
        raise StorageError('远程记录的 JSON 结构不正确，已停止覆盖。')
    return data, record['sha']


def load_json_from_github(path='chapter_structure.json'):
    token, repo, branch = _context()
    path = _path(path)
    data, sha = _read_json(token, repo, branch, path)
    if data is None:
        # 仅在已验证仓库/分支且文件确实不存在时，用随项目部署的课程作为初始值。
        bundled = (ROOT / path).resolve()
        if bundled.is_relative_to(ROOT) and bundled.is_file():
            try:
                data = json.loads(bundled.read_text(encoding='utf-8-sig'))
            except (ValueError, UnicodeError):
                raise StorageError('项目中的初始课程 JSON 损坏，请从备份恢复。') from None
        else:
            data = {} if path == 'chapter_structure.json' else []
        expected = dict if path == 'chapter_structure.json' else list
        if not isinstance(data, expected):
            raise StorageError('项目中的初始 JSON 结构不正确。')
    st.session_state[_snapshot_key(repo, branch, path)] = sha
    return data, sha


def save_json_to_github(data, path='chapter_structure.json'):
    token, repo, branch = _context()
    path = _path(path)
    expected = dict if path == 'chapter_structure.json' else list
    if not isinstance(data, expected):
        raise StorageError('待保存的 JSON 结构不正确。')
    key = _snapshot_key(repo, branch, path)
    if key not in st.session_state:
        raise StorageError('缺少课程读取记录，请刷新页面后重新保存，避免覆盖其他人的修改。')
    raw = _json_bytes(data)
    latest_data, latest_sha = _read_json(token, repo, branch, path)
    if latest_data == data:
        st.session_state[key] = latest_sha
        return True
    if latest_sha != st.session_state[key]:
        raise StorageError('课程已被其他操作修改，本次未覆盖；请刷新后重新上传或编辑。')
    payload = {'message': f'Update {path}', 'content': base64.b64encode(raw).decode(), 'branch': branch}
    if latest_sha is not None:
        payload['sha'] = latest_sha
    try:
        result = _request('PUT', repo, 'contents/' + quote(path, safe='/'), token=token, json=payload).json()
        if result.get('content', {}).get('sha') != _git_sha(raw):
            raise StorageError('课程保存响应校验失败，请重试核查。')
    except StorageError:
        confirmed, confirmed_sha = _read_json(token, repo, branch, path)
        if confirmed != data:
            raise
        st.session_state[key] = confirmed_sha
        return True
    st.session_state[key] = _git_sha(raw)
    return True


def download_file(file_ref):
    if not isinstance(file_ref, str) or not file_ref.strip():
        return None
    ref = file_ref.strip()
    if not ref.startswith(('https://', 'http://')):
        relative = ref.replace(chr(92), '/')
        candidate = (ROOT / relative).resolve()
        if not candidate.is_relative_to(ROOT):
            raise StorageError('图片路径超出项目目录。')
        if candidate.is_file():
            return candidate.read_bytes()
        # 未随应用部署的旧文件，可尝试从已配置的存储仓库读取。
        try:
            token, repo, branch = _context()
        except StorageConfigurationError:
            return None
        result = _content(token, repo, branch, relative, raw=True)
        return None if result is None else result.content
    parsed = urlparse(ref)
    if parsed.hostname == 'raw.githubusercontent.com':
        parts = parsed.path.lstrip('/').split('/', 3)
        if len(parts) == 4:
            owner, name, branch, path = parts
            active = _OPERATION_CONTEXT.get()
            if active is not None:
                token, repo, _ = active
            else:
                try:
                    token, repo, _ = _configuration()
                except StorageConfigurationError:
                    token, repo = None, None
            if repo and repo.lower() == f'{owner}/{name}'.lower():
                result = _content(token, repo, unquote(branch), unquote(path), raw=True)
                return None if result is None else result.content
    try:
        response = requests.get(ref, timeout=(10, 30))
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        raise StorageError('图片地址读取失败，请检查文件是否存在及访问权限。') from None


def show_image(file_ref, **kwargs):
    try:
        data = download_file(file_ref)
        if data is None:
            st.warning('未找到图片原文件。请检查仓库路径；旧文件缺失时需从备份恢复。')
        else:
            st.image(data, **kwargs)
    except (StorageError, OSError, ValueError):
        LOG.warning('图片读取或解码失败', exc_info=True)
        st.warning('图片暂时无法显示，请检查存储配置、文件格式或仓库权限。')


def display_course_image(file_ref, **kwargs):
    if file_ref:
        show_image(file_ref, **kwargs)
    else:
        st.caption('暂无图片')


def show_video(file_ref, **kwargs):
    if file_ref:
        try:
            data = download_file(file_ref)
            if data is None:
                st.warning('未找到视频原文件。')
            else:
                st.video(data, **kwargs)
        except (StorageError, OSError, ValueError):
            st.warning('视频读取失败，请检查路径或存储权限。')


def download_bytes(file_ref):
    return download_file(file_ref)



def storage_diagnostics():
    """验证真实连接及课程读取，不写文件，不返回密钥。"""
    token, repo, branch = _context()
    course, _ = _read_json(token, repo, branch, 'chapter_structure.json')
    return {'repo': repo, 'branch': branch, 'course_exists': course is not None,
            'chapter_count': len(course) if course is not None else 0}


def save_framework_image(uploaded_file, chapter, progress=None):
    """图片与章节引用都读回校验通过后，才确认框架图保存成功。"""
    stage = '读取课程记录'
    operation_token = None
    def report(message):
        nonlocal stage
        stage = message
        if progress is not None:
            progress(message)
    try:
        report('读取课程记录')
        operation_token = _OPERATION_CONTEXT.set(_context())
        course, _ = load_json_from_github('chapter_structure.json')
        if chapter not in course or not isinstance(course[chapter], dict):
            raise StorageError('该章节已不存在，请刷新章节列表后重试。')
        report('上传图片文件')
        ref = upload_file(uploaded_file, folder='framework-images')
        report('读回校验图片')
        uploaded_file.seek(0)
        expected = uploaded_file.read()
        actual = download_file(ref)
        if actual is None or hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
            raise StorageError('图片读回校验未通过，本次未更新章节引用；请重试。')
        report('保存课程中的图片地址')
        course[chapter]['framework_image'] = ref
        save_json_to_github(course, 'chapter_structure.json')
        report('读回核对课程记录')
        confirmed, _ = load_json_from_github('chapter_structure.json')
        if confirmed.get(chapter, {}).get('framework_image') != ref:
            raise StorageError('课程记录与本次上传不一致，可能存在其他修改，请刷新核查。')
        return ref
    except StorageError as exc:
        raise StorageError(f'{stage}失败：{exc}') from None
    finally:
        if operation_token is not None:
            _OPERATION_CONTEXT.reset(operation_token)
