import os
import uuid
from pathlib import Path

import streamlit as st
from supabase import create_client


@st.cache_resource
def get_storage_client():
    cfg = st.secrets["storage"]
    return create_client(cfg["url"], cfg["key"])


def upload_file(uploaded_file, folder="course"):
    client = get_storage_client()
    bucket = st.secrets["storage"]["bucket"]

    suffix = Path(uploaded_file.name).suffix.lower()
    object_key = f"{folder}/{uuid.uuid4().hex}{suffix}"

    uploaded_file.seek(0)
    content = uploaded_file.getvalue()

    client.storage.from_(bucket).upload(
        path=object_key,
        file=content,
        file_options={
            "content-type": uploaded_file.type or "application/octet-stream",
            "upsert": "false",
        },
    )

    return f"sb://{object_key}"


def download_file(file_ref):
    if not file_ref or not str(file_ref).startswith("sb://"):
        raise ValueError("不是有效的云存储文件标识")
    object_key = str(file_ref)[len("sb://"):]
    bucket = st.secrets["storage"]["bucket"]
    client = get_storage_client()
    return client.storage.from_(bucket).download(object_key)


def show_image(file_ref, **kwargs):
    try:
        data = download_file(file_ref)
        st.image(data, **kwargs)
    except Exception as e:
        st.warning(f"图片暂时无法读取：{e}")


def display_course_image(file_ref, **kwargs):
    if not file_ref:
        st.caption("暂无图片")
    elif str(file_ref).startswith("sb://"):
        show_image(file_ref, **kwargs)
    elif os.path.isfile(str(file_ref)):
        st.image(file_ref, **kwargs)
    else:
        st.warning(f"文件不可用：{os.path.basename(str(file_ref))}")


def show_video(file_ref, **kwargs):
    try:
        data = download_file(file_ref)
        st.video(data, **kwargs)
    except Exception as e:
        st.warning(f"视频暂时无法读取：{e}")


def download_bytes(file_ref):
    return download_file(file_ref)
