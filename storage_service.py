import os
import base64
import uuid
import requests
import streamlit as st
import os

def show_secrets_debug(stage):
    try:
        keys = list(st.secrets.keys())
        st.write(f"[调试] {stage}：{keys}")
    except Exception as e:
        st.write(f"[调试] {stage}：读取失败（{type(e).__name__}）")




def upload_file(uploaded_file, folder="images", token=None, repo=None):
    import os
    import base64
    import uuid
    import requests
    import streamlit as st

    # 兼容旧调用，但优先使用 app.py 传入的配置
    if not token or not repo:
        try:
            cfg = st.secrets["github_storage"]
            token = token or cfg["token"]
            repo = repo or cfg["repo"].strip()
        except Exception as e:
            raise RuntimeError(
                f"无法读取 GitHub 配置：{type(e).__name__}: {e}"
            ) from e

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        repo_url = f"https://api.github.com/repos/{repo}"
        repo_resp = requests.get(repo_url, headers=headers, timeout=15)
        repo_resp.raise_for_status()
        branch = repo_resp.json()["default_branch"]

        ext = os.path.splitext(uploaded_file.name)[1].lower()
        filename = f"{folder}/{uuid.uuid4().hex}{ext}"

        uploaded_file.seek(0)
        content_b64 = base64.b64encode(
            uploaded_file.read()
        ).decode("utf-8")

        url = f"{repo_url}/contents/{filename}"
        data = {
            "message": f"upload {filename}",
            "content": content_b64,
            "branch": branch,
        }

        resp = requests.put(
            url, headers=headers, json=data, timeout=30
        )
        resp.raise_for_status()

        return (
            f"https://raw.githubusercontent.com/"
            f"{repo}/{branch}/{filename}"
        )

    except Exception as e:
        detail = (
            e.response.text
            if isinstance(e, requests.HTTPError)
            and e.response is not None
            else str(e)
        )
        raise RuntimeError(f"GitHub 图片上传失败：{detail}") from e



def download_file(file_ref):
    """下载图片字节流"""
    if not file_ref:
        raise ValueError("文件路径为空")
    resp = requests.get(file_ref, timeout=15)
    if resp.status_code == 200:
        return resp.content
    else:
        raise Exception(f"下载失败：{resp.status_code}")


def show_image(file_ref, **kwargs):
    try:
        data = download_file(file_ref)
        st.image(data, **kwargs)
    except Exception:
        pass


def display_course_image(file_ref, **kwargs):
    if not file_ref:
        st.caption("暂无图片")
    elif str(file_ref).startswith("http"):
        show_image(file_ref, **kwargs)
    elif os.path.isfile(str(file_ref)):
        st.image(file_ref, **kwargs)
    else:
        pass


def show_video(file_ref, **kwargs):
    try:
        data = download_file(file_ref)
        st.video(data, **kwargs)
    except Exception:
        pass


def download_bytes(file_ref):
    return download_file(file_ref)
