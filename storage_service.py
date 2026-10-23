import os
import base64
import uuid
import requests
import streamlit as st


@st.cache_resource
def get_github_config():
    cfg = st.secrets["github_storage"]
    return cfg["token"], cfg["repo"]


def upload_file(uploaded_file, folder="images"):
    """上传文件到 GitHub，返回 raw 图片链接"""
    token, repo = get_github_config()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    try:
        # 获取仓库信息和默认分支
        repo_url = f"https://api.github.com/repos/{repo}"
        repo_resp = requests.get(
            repo_url, headers=headers, timeout=15
        )
        st.info("正在连接 GitHub，检查仓库权限……")
        st.write("正在检查的仓库：", repo)
        st.write("GitHub 返回状态码：", repo_resp.status_code)
        st.write("GitHub 返回内容：", repo_resp.text)
        repo_resp.raise_for_status()
        branch = repo_resp.json()["default_branch"]

        # 生成文件路径
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
            "branch": branch
        }

        st.info("已连接 GitHub，正在上传图片……")
        resp = requests.put(
            url, headers=headers, json=data, timeout=30
        )
        resp.raise_for_status()
        st.success("GitHub 已接收图片")

        return (
            f"https://raw.githubusercontent.com/"
            f"{repo}/{branch}/{filename}"
        )

    except Exception as e:
        if isinstance(e, requests.HTTPError) and e.response is not None:
            detail = e.response.text
        else:
            detail = str(e)

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
