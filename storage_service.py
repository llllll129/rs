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
    """上传到 GitHub 仓库，并返回 raw 链接"""
    token, repo = get_github_config()
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    filename = f"{folder}/{uuid.uuid4().hex}{ext}"
    
    url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    uploaded_file.seek(0)
    content_b64 = base64.b64encode(uploaded_file.read()).decode("utf-8")
    
    data = {
        "message": f"upload {filename}",
        "content": content_b64
    }
    
    try:
        resp = requests.put(url, headers=headers, json=data, timeout=15)
        if resp.status_code in [200, 201]:
            # 返回可以直接访问的 raw 链接
            raw_url = f"https://raw.githubusercontent.com/{repo}/master/{filename}"
            return raw_url
        else:
            raise Exception(f"GitHub API 报错：{resp.status_code} - {resp.text}")
    except Exception as e:
        raise Exception(f"上传失败：{str(e)}")


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
