
import os
from pathlib import Path
from urllib.parse import urlparse, unquote, quote
import base64
import uuid
import json
import requests
import streamlit as st

def get_github_config():
    cfg = st.secrets["github_storage"]
    return cfg["token"], cfg["repo"].strip()


def github_request(method, path, **kwargs):
    token, repo = get_github_config()

    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    response = requests.request(
        method, url, headers=headers, timeout=30, **kwargs
    )
    response.raise_for_status()
    return response.json()


def upload_file(uploaded_file, folder="images", token=None, repo=None):
    if not token or not repo:
        token, repo = get_github_config()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    repo_url = f"https://api.github.com/repos/{repo}"
    repo_resp = requests.get(repo_url, headers=headers, timeout=15)
    if not repo_resp.ok:
        raise RuntimeError(
        f"GitHub 上传失败，状态码：{repo_resp.status_code}；"
        f"原因：{repo_resp.text}"
    )
    branch = repo_resp.json()["default_branch"]

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    filename = f"{folder}/{uuid.uuid4().hex}{ext}"

    uploaded_file.seek(0)
    content = base64.b64encode(uploaded_file.read()).decode("utf-8")

    url = f"{repo_url}/contents/{filename}"
    response = requests.put(
        url,
        headers=headers,
        json={
            "message": f"Upload image {filename}",
            "content": content,
            "branch": branch,
        },
        timeout=30,
    )
    response.raise_for_status()

    return f'https://raw.githubusercontent.com/{repo}/{quote(branch, safe="")}/{filename}'


def load_json_from_github(path="chapter_structure.json"):
    try:
        result = github_request("GET", path)
        content = base64.b64decode(result["content"]).decode("utf-8")
        return json.loads(content), result["sha"]
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {}, None
        raise


def save_json_to_github(data, path="chapter_structure.json"):
    token, repo = get_github_config()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    repo_url = f"https://api.github.com/repos/{repo}"
    repo_resp = requests.get(repo_url, headers=headers, timeout=15)
    repo_resp.raise_for_status()
    branch = repo_resp.json()["default_branch"]

    url = f"{repo_url}/contents/{path}"
    old = requests.get(
        url, headers=headers, params={"ref": branch}, timeout=15
    )

    payload = {
        "message": f"Update {path}",
        "content": base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("utf-8"),
        "branch": branch,
    }

    if old.status_code == 200:
        payload["sha"] = old.json()["sha"]
    elif old.status_code != 404:
        old.raise_for_status()

    response = requests.put(
        url, headers=headers, json=payload, timeout=30
    )
    response.raise_for_status()
    return True


def download_file(file_ref):
    """读取远程文件或项目内旧文件；缺失的原文件不能由代码恢复。"""
    if not isinstance(file_ref, str) or not file_ref.strip():
        return None
    file_ref = file_ref.strip()
    if not file_ref.startswith(("https://", "http://")):
        # 兼容 Windows 旧路径；限制读取范围在项目内。
        root = Path(__file__).resolve().parent
        target = (root / file_ref.replace(chr(92), "/")).resolve()
        if not target.is_relative_to(root):
            raise ValueError("文件路径必须位于项目目录内")
        return target.read_bytes() if target.is_file() else None
    parsed = urlparse(file_ref)
    if parsed.hostname == "raw.githubusercontent.com":
        # 对配置的图片仓库通过认证 API 下载，兼容私有仓库。
        try:
            token, repo = get_github_config()
        except (KeyError, FileNotFoundError):
            token, repo = None, None
        prefix = f"/{repo}/" if repo else None
        if prefix and parsed.path.startswith(prefix):
            branch, sep, path = parsed.path[len(prefix):].partition("/")
            if sep:
                response = requests.get(
                    f"https://api.github.com/repos/{repo}/contents/{path}",
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github.raw+json"},
                    params={"ref": unquote(branch)}, timeout=20,
                )
                response.raise_for_status()
                return response.content
    response = requests.get(file_ref, timeout=20)
    response.raise_for_status()
    return response.content


def show_image(file_ref, **kwargs):
    try:
        image_data = download_file(file_ref)
        if image_data is None:
            st.warning("找不到图片原文件，请检查是否已提交到仓库；缺失文件需从备份恢复。")
        else:
            st.image(image_data, **kwargs)
    except (requests.RequestException, OSError, ValueError) as exc:
        st.warning(f"图片读取失败（{type(exc).__name__}），请检查路径、仓库权限或网络。")


def display_course_image(file_ref, **kwargs):
    if file_ref:
        show_image(file_ref, **kwargs)
    else:
        st.caption("暂无图片")


def show_video(file_ref, **kwargs):
    if file_ref:
        st.video(download_file(file_ref), **kwargs)


def download_bytes(file_ref):
    return download_file(file_ref)
