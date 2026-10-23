
import os
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
    repo_resp.raise_for_status()
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

    return f"https://raw.githubusercontent.com/{repo}/{branch}/{filename}"


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
    response = requests.get(file_ref, timeout=20)
    response.raise_for_status()
    return response.content


def show_image(file_ref, **kwargs):
    if file_ref:
        st.image(download_file(file_ref), **kwargs)


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
