# app.py
import os
import json
import re
import numpy as np
from datetime import datetime
import streamlit as st
from chromadb import PersistentClient
from openai import OpenAI

st.set_page_config(page_title="遥感课程智能助教", page_icon="🛰️", layout="wide")

TEACHER_PASSWORD = "rs2026"
LOG_FILE = "learning_log.jsonl"
QUIZ_FILE = "quiz_tasks.json"
PAPER_FILE = "papers.json"
CASE_FILE = "cases.json"
CHAPTER_STRUCTURE_FILE = "chapter_structure.json"
STUDY_LOG_FILE = "study_progress.json"
CLASS_QA_FILE = "class_qa.json"
UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

st.markdown("""
<style>
    .top-bar { background: linear-gradient(135deg, #1a237e 0%, #283593 100%); padding: 12px 24px; border-radius: 12px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; color: white; }
    .top-bar .course-name { font-size: 1.3em; font-weight: bold; }
    .top-bar .user-info { display: flex; align-items: center; gap: 10px; font-size: 0.95em; }
    .user-avatar { width: 36px; height: 36px; border-radius: 50%; background: #ffd54f; color: #333; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 1.1em; }
    .kp-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 16px; }
</style>
""", unsafe_allow_html=True)

API_KEY = "sk-8c8010eb5e9541b5a9db0c6df557fa7c"

@st.cache_resource
def init():
    class AliyunEmbedding:
        def encode(self, texts):
            if isinstance(texts, str): texts = [texts]
            embeddings = []
            for text in texts:
                resp = llm_client.embeddings.create(model="text-embedding-v2", input=text)
                embeddings.append(resp.data[0].embedding)
            return np.array(embeddings)

    model = AliyunEmbedding()

    import zipfile
    if not os.path.exists("rs_knowledge_db") and os.path.exists("rs_knowledge_db.zip"):
        with zipfile.ZipFile("rs_knowledge_db.zip", "r") as zf:
            zf.extractall(".")

    chroma_client = PersistentClient(path="rs_knowledge_db")
    collection = chroma_client.get_collection("rs_course")
    llm = OpenAI(api_key=API_KEY, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")

    global llm_client
    llm_client = OpenAI(api_key=API_KEY, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")

    return model, collection, llm

model, collection, llm = init()


def get_all_chapters():
    results = collection.get()
    chapters = set()
    for meta in results["metadatas"]: chapters.add(meta.get("chapter", "未知"))
    return sorted(chapters)


def search_textbook(keywords, chapter_filter=None, n_results=3):
    embedding = model.encode(keywords).tolist()
    if chapter_filter:
        results = collection.query(query_embeddings=embedding, n_results=20)
        docs = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            if meta.get("chapter") == chapter_filter and len(doc.strip()) > 50:
                docs.append(doc)
            if len(docs) >= n_results: break
        if not docs:
            results = collection.query(query_embeddings=embedding, n_results=n_results)
            docs = [d for d, m in zip(results["documents"][0], results["metadatas"][0]) if len(d.strip()) > 50]
        return docs[:n_results]
    else:
        results = collection.query(query_embeddings=embedding, n_results=n_results)
        return [d for d in results["documents"][0] if len(d.strip()) > 50]


def search_and_format_textbook(keywords, chapter_filter=None, n_results=4):
    docs = search_textbook(keywords, chapter_filter=chapter_filter, n_results=n_results)
    raw_text = "\n\n".join(docs)
    sp = f"""你是遥感课程教材编辑。请根据课本原文，解释【{keywords}】这个知识点。
输出要求：
1. 先给出该概念的准确定义（1-2句话）
2. 列出核心要点（2-4条，用编号列出）
3. 如果课本原文中涉及公式，请写出公式并解释每个符号的含义；如果没有公式，这部分直接省略，不要硬编
4. 整体简洁清晰，严格基于课本原文，修正OCR导致的断行和错别字"""
    try:
        formatted = call_llm(sp, f"请根据以下课本原文解释【{keywords}】：\n{raw_text}", max_tokens=1200)
        formatted = re.sub(r'^#{1,6}\s+', '', formatted, flags=re.MULTILINE)
        return formatted
    except:
        cleaned = re.sub(r'^#{1,6}\s+', '', raw_text, flags=re.MULTILINE)
        return cleaned


def call_llm(system_prompt, user_content, max_tokens=800):
    response = llm.chat.completions.create(model="qwen-turbo", messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ], temperature=0.3, max_tokens=max_tokens)
    return response.choices[0].message.content


def load_json(path):
    if not os.path.exists(path):
        return {} if path in [STUDY_LOG_FILE, CHAPTER_STRUCTURE_FILE] else []
    with open(path, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return {} if path in [STUDY_LOG_FILE, CHAPTER_STRUCTURE_FILE] else []


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_think_question(kp_title, textbook_content, chapter):
    sp = """你是遥感课程教师。请根据该知识点的课本内容，出一道深层次的思考题。
要求：1. 不要简单重复概念，要引导学生深入理解原理
2. 可以结合实际应用场景提问 3. 可以让学生推导或解释某个现象的本质原因
4. 只输出题目本身，1-2句话，不要加任何前缀"""
    return call_llm(sp, f"【章节】{chapter}\n【知识点】{kp_title}\n【课本内容】\n{textbook_content}", max_tokens=200)


def evaluate_think_answer(question, student_answer, chapter):
    return call_llm("评价学生的思考题回答。输出：【评分】XX分\n【评语】XXX",
                    f"【章节】{chapter}\n【思考题】{question}\n【学生答案】{student_answer}", max_tokens=500)


def generate_quiz_questions(chapter, question_type, count=3):
    context = "\n".join(search_textbook(f"{chapter} 核心知识点", chapter_filter=chapter, n_results=10))
    if question_type == "选择题":
        sp = f"""生成{count}道单选题，考察对概念的理解和应用能力，非简单记忆。
要求：1. 选项要有干扰性 2. 难度适中偏难 3. 格式：题目N：xxx\nA. xxx  B. xxx  C. xxx  D. xxx\n答案：X（正确选项字母）\n正确选项内容：xxx\n解析：xxx"""
    elif question_type == "填空题":
        sp = f"""生成{count}道填空题，考察关键概念和公式的理解。
要求：1. 用______标记空白处 2. 不要过于简单 3. 格式：题目N：xxx\n答案：xxx"""
    else:
        sp = f"""生成{count}道简答题，考察分析能力和综合理解。
要求：1. 需要一定的思考和推导 2. 非直接照搬课本 3. 格式：题目N：xxx\n参考答案要点：xxx"""
    return call_llm(sp, f"【课本】\n{context}\n\n{question_type}，章节：{chapter}", max_tokens=2000)


def auto_grade_quiz(question, correct_answer, student_answer, q_type):
    if q_type == "选择题":
        correct = correct_answer.strip()[0].upper()
        student = student_answer.strip().upper()
        return (100, "正确") if correct == student else (0, "错误")
    elif q_type == "填空题":
        return (100, "正确") if student_answer.strip() == correct_answer.strip() else (60, f"部分正确，参考答案：{correct_answer}")
    else:
        result = call_llm("评判简答题，输出：【得分】XX分\n【评价】XXX",
                          f"【参考答案】\n{correct_answer}\n\n【学生答案】\n{student_answer}", max_tokens=300)
        score = 60
        for line in result.split("\n"):
            if "【得分】" in line:
                try: score = int(line.split("【得分】")[1].split("分")[0].strip())
                except: pass
        return score, result


def log_interaction(log_type, data):
    data["timestamp"] = datetime.now().isoformat()
    data["log_type"] = log_type
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def analyze_logs_by_type(log_type):
    if not os.path.exists(LOG_FILE): return []
    records = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("log_type") == log_type: records.append(r)
    return records

# ==================== 页面 ====================
if "role" not in st.session_state:
    st.session_state.role = "👨‍🎓 学生端"

role = st.sidebar.radio("选择角色：", ["👨‍🎓 学生端", "👩‍🏫 教师端"], key="role_select")
st.session_state.role = role


# ==================== 学生端 ====================
if role == "👨‍🎓 学生端":
    if "student_logged_in" not in st.session_state: st.session_state.student_logged_in = False
    if not st.session_state.student_logged_in:
        st.markdown("## 🔑 学生登录")
        col_login, _ = st.columns([1, 2])
        with col_login:
            sid = st.text_input("学号", key="login_sid")
            sname = st.text_input("姓名", key="login_sname")
            if st.button("登录", type="primary") and sid and sname:
                st.session_state.student_id = sid
                st.session_state.student_name = sname
                st.session_state.student_key = f"{sid}_{sname}"
                st.session_state.student_logged_in = True
                st.rerun()
        st.stop()

    student_id = st.session_state.student_id
    student_name = st.session_state.student_name
    student_key = st.session_state.student_key

    st.markdown(f"""<div class="top-bar"><span class="course-name">🛰️ 遥感技术原理与方法</span><div class="user-info"><span>{student_name}</span><div class="user-avatar">{student_name[0]}</div></div></div>""", unsafe_allow_html=True)

    tab_labels = ["📚 章节学习", "📋 课堂问答", "📝 章节检测", "📄 论文推荐", "🔬 ENVI实例", "💬 操作求助"]
    if "active_tab" not in st.session_state: st.session_state.active_tab = 0
    active = st.session_state.active_tab

    cols = st.columns(len(tab_labels))
    for i, (col, label) in enumerate(zip(cols, tab_labels)):
        with col:
            if st.button(label, key=f"btab_{i}", type="primary" if i == active else "secondary", use_container_width=True):
                st.session_state.active_tab = i; st.rerun()
    st.markdown("---")

    if active == 0:
        structure = load_json(CHAPTER_STRUCTURE_FILE)
        if not structure: st.info("📭 老师暂未搭建章节结构")
        else:
            sel_ch = st.selectbox("📖 选择章节：", list(structure.keys()), key="ch_sel", label_visibility="collapsed")
            ch_data = structure[sel_ch]
            all_kps = []
            for si, sec in enumerate(ch_data.get("sections", [])):
                for ki, kp in enumerate(sec.get("knowledge_points", [])):
                    tb_imgs = kp.get("textbook_images", [])
                    if not tb_imgs and kp.get("textbook_image"): tb_imgs = [kp["textbook_image"]]
                    all_kps.append({
                        "section_title": sec["title"], "kp_title": kp["title"],
                        "textbook_content": kp.get("textbook_content", ""),
                        "textbook_images": tb_imgs,
                        "selected_examples": kp.get("selected_examples", []),
                        "think_question": kp.get("think_question", ""),
                        "sec_idx": si, "kp_idx": ki
                    })
            if not all_kps: st.info("本章暂无知识点")
            else:
                study_log = load_json(STUDY_LOG_FILE)
                if "students" not in study_log: study_log["students"] = {}
                if student_key not in study_log["students"]: study_log["students"][student_key] = {}
                my_log = study_log["students"][student_key]
                if sel_ch not in my_log: my_log[sel_ch] = {"learned": [], "current_idx": 0}
                learned = set(my_log[sel_ch]["learned"])
                current_idx = my_log[sel_ch]["current_idx"]
                total = len(all_kps)
                learned_count = len(learned)
                pct = int(learned_count / total * 100) if total else 0

                left_col, right_col = st.columns([1, 1])
                with left_col:
                    if ch_data.get("framework_image"):
                        try: st.image(ch_data["framework_image"], use_container_width=True)
                        except: st.info("📊 框架图缺失")
                    else: st.info("📊 暂无框架图")
                with right_col:
                    if learned_count >= total: st.success("🎉 本章所有知识点已学习完毕！")
                    if current_idx >= total: st.info("已是最后一个知识点")
                    else:
                        kp = all_kps[current_idx]
                        st.caption(f"📖 {sel_ch} > {kp['section_title']}")
                        st.markdown(f"""<div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:10px 18px;border-radius:10px;color:white;margin-bottom:12px"><span style="font-size:1.1em;font-weight:bold">🔹 {kp['kp_title']}</span></div>""", unsafe_allow_html=True)
                        progress_val = min(learned_count / total, 1.0) if total > 0 else 0.0
                        st.progress(progress_val, text=f"学习进度 {min(learned_count, total)}/{total}（{min(pct, 100)}%）")
                        st.markdown('<div class="kp-card">', unsafe_allow_html=True)
                        st.markdown("##### 📖 课本原文")
                        st.text(kp["textbook_content"] or "暂无内容")
                        for img_path in kp.get("textbook_images", []):
                            if img_path and os.path.exists(img_path):
                                try: st.image(img_path, use_container_width=True)
                                except: pass
                        st.markdown('</div>', unsafe_allow_html=True)
                        if kp["selected_examples"]:
                            with st.expander("##### 🌰 生活实例"):
                                for ex in kp["selected_examples"]:
                                    if isinstance(ex, dict):
                                        if ex.get("type") == "image" and ex.get("content"):
                                            try: st.image(ex["content"])
                                            except: st.caption("图片缺失")
                                        elif ex.get("type") == "video" and ex.get("content"): st.video(ex["content"])
                                        elif ex.get("content"): st.info(ex["content"])
                                    elif ex: st.info(ex)
                        if kp.get("think_question"):
                            st.markdown('<div class="kp-card">', unsafe_allow_html=True)
                            st.markdown("##### 🤔 思考题")
                            st.info(f"**{kp['think_question']}**")
                            think_answer = st.text_area("✍️ 你的回答：", height=80, key=f"think_{sel_ch}_{current_idx}")
                            if st.button("📤 提交思考题", key=f"submit_think_{sel_ch}_{current_idx}") and think_answer:
                                with st.spinner("评阅中..."): ev = evaluate_think_answer(kp['think_question'], think_answer, sel_ch)
                                log_interaction("think", {"student_id": student_id, "student_name": student_name, "chapter": sel_ch, "kp_title": kp['kp_title'], "think_question": kp['think_question'], "student_answer": think_answer, "evaluation": ev})
                                st.success("✅ 已提交")
                            st.markdown('</div>', unsafe_allow_html=True)
                        st.markdown("---")
                        col1, col2, col3 = st.columns([1, 1, 1])
                        with col1:
                            if st.button("⬅️ 上一个", disabled=(current_idx == 0), use_container_width=True):
                                my_log[sel_ch]["current_idx"] = current_idx - 1; save_json(study_log, STUDY_LOG_FILE); st.rerun()
                        with col2:
                            if current_idx in learned: st.success("✅ 已学")
                            elif st.button("🎯 标记已学", type="primary", use_container_width=True):
                                kp = all_kps[current_idx]
                                think_q = kp.get('think_question', '')
                                if think_q:
                                    submitted = any(r.get('chapter') == sel_ch and r.get('kp_title') == kp['kp_title']
                                                   for r in analyze_logs_by_type("think")
                                                   if f"{r.get('student_id','')}_{r.get('student_name','')}" == student_key)
                                    if not submitted: st.warning("⚠️ 请先提交该知识点的思考题"); st.stop()
                                learned.add(current_idx); my_log[sel_ch]["learned"] = list(learned)
                                if current_idx < total - 1: my_log[sel_ch]["current_idx"] = current_idx + 1
                                save_json(study_log, STUDY_LOG_FILE); st.rerun()
                        with col3:
                            if st.button("➡️ 下一个", disabled=(current_idx == total - 1), use_container_width=True):
                                my_log[sel_ch]["current_idx"] = current_idx + 1; save_json(study_log, STUDY_LOG_FILE); st.rerun()

    elif active == 1:
        st.subheader("📋 课堂问答")
        class_qa = load_json(CLASS_QA_FILE)
        if not isinstance(class_qa, list) or not class_qa: st.info("📭 暂无")
        else:
            qa_chapters = list(set(q["chapter"] for q in class_qa))
            sel_qa_ch = st.selectbox("选择章节：", qa_chapters, key="student_qa_ch")
            filtered_qa = [q for q in class_qa if q["chapter"] == sel_qa_ch]
            pre_qa = [q for q in filtered_qa if q.get('type') == '课前预习检测']
            mid_qa = [q for q in filtered_qa if q.get('type') == '课中随堂检测']

            if pre_qa:
                st.markdown("## 📖 课前预习检测")
                for idx, item in enumerate(pre_qa):
                    with st.expander(f"问题{idx+1}", expanded=(idx==0)):
                        st.write(item['question'])
                        qtype = item.get('q_type', '简答题')
                        submit_key = f"pre_qa_submitted_{sel_qa_ch}_{idx}"
                        if submit_key not in st.session_state: st.session_state[submit_key] = False
                        if st.session_state[submit_key]: st.success("✅ 已提交")
                        else:
                            stu_qa_answer = st.radio("你的答案：", ["A","B","C","D"], key=f"pre_qa_{sel_qa_ch}_{idx}") if qtype == "选择题" else st.text_area("✍️ 你的回答：", height=100, key=f"pre_qa_{sel_qa_ch}_{idx}")
                            if st.button(f"📤 提交", key=f"submit_pre_qa_{sel_qa_ch}_{idx}") and stu_qa_answer:
                                st.session_state[submit_key] = True
                                if qtype == "选择题":
                                    correct = item.get('reference','').strip()[0].upper()
                                    ev, s = ("正确", 100) if stu_qa_answer == correct else ("错误", 0)
                                    st.write(f"{'✅ 正确' if s >= 100 else '❌ 错误'}")
                                else:
                                    s, fb = auto_grade_quiz(item['question'], item.get('reference',''), stu_qa_answer, qtype)
                                    ev = fb; st.success("✅ 已提交")
                                log_interaction("class_qa", {"student_id": student_id, "student_name": student_name, "chapter": sel_qa_ch, "type": item['type'], "q_type": qtype, "question": item['question'], "student_answer": stu_qa_answer, "score": s, "evaluation": ev})
                                st.rerun()

            if mid_qa:
                st.markdown("---"); st.markdown("## 📝 课中随堂检测")
                for idx, item in enumerate(mid_qa):
                    with st.expander(f"问题{idx+1}", expanded=(idx==0)):
                        st.write(item['question'])
                        qtype = item.get('q_type', '简答题')
                        submit_key = f"mid_qa_submitted_{sel_qa_ch}_{idx}"
                        if submit_key not in st.session_state: st.session_state[submit_key] = False
                        if st.session_state[submit_key]: st.success("✅ 已提交")
                        else:
                            stu_qa_answer = st.radio("你的答案：", ["A","B","C","D"], key=f"mid_qa_{sel_qa_ch}_{idx}") if qtype == "选择题" else st.text_area("✍️ 你的回答：", height=100, key=f"mid_qa_{sel_qa_ch}_{idx}")
                            if st.button(f"📤 提交", key=f"submit_mid_qa_{sel_qa_ch}_{idx}") and stu_qa_answer:
                                st.session_state[submit_key] = True
                                if qtype == "选择题":
                                    correct = item.get('reference','').strip()[0].upper()
                                    ev, s = ("正确", 100) if stu_qa_answer == correct else ("错误", 0)
                                    st.write(f"{'✅ 正确' if s >= 100 else '❌ 错误'}")
                                else:
                                    s, fb = auto_grade_quiz(item['question'], item.get('reference',''), stu_qa_answer, qtype)
                                    ev = fb; st.success("✅ 已提交")
                                log_interaction("class_qa", {"student_id": student_id, "student_name": student_name, "chapter": sel_qa_ch, "type": item['type'], "q_type": qtype, "question": item['question'], "student_answer": stu_qa_answer, "score": s, "evaluation": ev})
                                st.rerun()

    elif active == 2:
        st.subheader("📝 章节检测")
        structure = load_json(CHAPTER_STRUCTURE_FILE)
        quizzes = load_json(QUIZ_FILE)
        if not isinstance(quizzes, list) or not quizzes: st.info("暂无检测题")
        else:
            study_log = load_json(STUDY_LOG_FILE); my_log = study_log.get("students", {}).get(student_key, {})
            names, available = [], []
            for q in quizzes:
                ch = q["chapter"]; ch_data = structure.get(ch, {})
                total_kps = sum(len(sec.get("knowledge_points", [])) for sec in ch_data.get("sections", []))
                can = (total_kps == 0) or (len(my_log.get(ch, {}).get("learned", [])) >= total_kps)
                q_type_info = q.get("q_type", "混合")
                names.append(f"{ch}（{q_type_info}，{len(q['questions'])}题）{'✅' if can else '🔒'}"); available.append((q, can))
            sel_idx = st.selectbox("选择检测：", range(len(names)), format_func=lambda i: names[i])
            quiz, can = available[sel_idx]
            if not can: st.warning("🔒 请先完成学习")
            else:
                st.markdown(f"### {quiz['chapter']} 章节检测")
                for i, qi in enumerate(quiz["questions"]):
                    q_type = qi.get("q_type", "简答题")
                    st.markdown(f"**第{i+1}题**（{q_type}）：{qi['question']}")
                    submit_key = f"quiz_submitted_{sel_idx}_{i}"
                    if submit_key not in st.session_state: st.session_state[submit_key] = False
                    if st.session_state[submit_key]: st.success("✅ 已提交")
                    else:
                        if q_type == "选择题": stu = st.radio("答案：", ["A","B","C","D"], key=f"qz_{i}")
                        elif q_type == "填空题": stu = st.text_area("填空答案：", height=68, key=f"qz_{i}")
                        else: stu = st.text_area("回答：", height=80, key=f"qz_{i}")
                        if st.button(f"提交第{i+1}题", key=f"qsub_{i}"):
                            st.session_state[submit_key] = True
                            if q_type == "选择题":
                                question_text = qi['question']
                                option_lines = [l for l in question_text.split("\n") if l.strip().startswith(("A.","B.","C.","D."))]
                                options = {}
                                for ol in option_lines:
                                    if len(ol) >= 2 and ol[1] == ".": options[ol[0]] = ol[2:].strip()
                                selected_content = options.get(stu, "")
                                correct_content = qi['answer'].strip()
                                if selected_content and correct_content and (selected_content == correct_content or selected_content[:10] == correct_content[:10]): s, fb = 100, "正确"
                                else: s, fb = 0, "错误"
                                st.write(f"{'✅ 正确' if s >= 100 else '❌ 错误'}")
                            else:
                                s, fb = auto_grade_quiz(qi['question'], qi['answer'], stu, q_type)
                                st.success("✅ 已提交")
                            log_interaction("quiz", {"student_id": student_id, "student_name": student_name, "chapter": quiz['chapter'], "question": qi['question'], "q_type": q_type, "correct_answer": qi['answer'], "student_answer": stu, "score": s, "feedback": fb})
                            st.rerun()

    elif active == 3:
        st.subheader("📄 论文推荐")
        papers = load_json(PAPER_FILE)
        if not isinstance(papers, list) or not papers: st.info("暂无")
        else:
            chapters = list(set(p["chapter"] for p in papers))
            sel = st.selectbox("章节：", chapters)
            for p in papers:
                if p["chapter"] == sel:
                    if p.get("is_file"):
                        with st.expander(f"📄 {p['title']}"):
                            if os.path.exists(p.get('file_path', '')):
                                if p['title'].lower().endswith('.pdf'):
                                    with open(p['file_path'], "rb") as f: st.download_button("📥 下载论文", f.read(), file_name=p['title'])
                                elif p['title'].lower().endswith('.txt'):
                                    with open(p['file_path'], "r", encoding="utf-8") as f: st.text_area("论文内容：", f.read(), height=300)
                                elif p['title'].lower().endswith(('.docx', '.doc')):
                                    try:
                                        from docx import Document
                                        doc = Document(p['file_path']); st.text_area("论文内容：", "\n".join([para.text for para in doc.paragraphs])[:5000], height=300)
                                    except: pass
                            else: st.caption("文件已失效")
                    else:
                        with st.expander(f"📄 {p['title']}"): st.write(f"**作者**：{p.get('author','')}，**摘要**：{p.get('abstract','')}")

    elif active == 4:
        st.subheader("🔬 ENVI 实例")
        cases = load_json(CASE_FILE)
        if not isinstance(cases, list) or not cases: st.info("暂无")
        else:
            sel = st.selectbox("章节：", list(set(c["chapter"] for c in cases)))
            for c in cases:
                if c["chapter"] == sel:
                    with st.expander(f"📁 {c['title']}"):
                        if c.get("is_video"): st.video(c.get("file_path",""))
                        else: st.markdown(c.get("content",""))

    elif active == 5:
        st.subheader("💬 操作求助")
        sel_ch = st.selectbox("章节：", get_all_chapters()); q = st.text_area("描述问题：", height=120)
        uf = st.file_uploader("上传截图", type=["png","jpg","jpeg"])
        if st.button("📤 提交") and q:
            sp = None
            if uf:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S"); sp = os.path.join(UPLOAD_DIR, f"{student_id}_{ts}_{uf.name}")
                with open(sp, "wb") as f: f.write(uf.getbuffer())
            ctx = "\n".join(search_textbook(q, chapter_filter=sel_ch))
            ans = call_llm("你是遥感助教。根据课本回答。末尾标注【问题已解决】或【需要老师查看】", f"【课本】\n{ctx}\n\n【问题】\n{q}")
            log_interaction("qa", {"student_id":student_id,"student_name":student_name,"chapter":sel_ch,"question":q,"answer":ans,"screenshot":sp or "","needs_teacher":"需要老师" in ans,"resolved":not ("需要老师" in ans)})
            st.write(ans)


# ==================== 教师端 ====================
elif role == "👩‍🏫 教师端":
    if "teacher_auth" not in st.session_state: st.session_state.teacher_auth = False
    if not st.session_state.teacher_auth:
        st.title("🔒 教师登录")
        pw = st.text_input("密码", type="password")
        if st.button("验证登录", key="teacher_login_btn"):
            if pw == TEACHER_PASSWORD: st.session_state.teacher_auth = True; st.rerun()
            else: st.error("密码错误")
        st.stop()

    st.markdown(f"""<div class="top-bar"><span class="course-name">🛰️ 遥感技术原理与方法 — 教师管理</span><div class="user-info"><span>教师</span><div class="user-avatar">教</div></div></div>""", unsafe_allow_html=True)
    if st.sidebar.button("🚪 退出"): st.session_state.teacher_auth = False; st.rerun()

    tabs = st.tabs(["📚 章节结构", "📋 课堂问答", "📝 章节检测", "📄 论文管理", "🔬 实例上传", "💬 问题管理", "📊 学情总览"])
    all_chapters = get_all_chapters()

    with tabs[0]:
        st.subheader("📚 章节结构管理")
        structure = load_json(CHAPTER_STRUCTURE_FILE)
        built = list(structure.keys())
        sel_ch = st.selectbox("选择章节：", built + ["➕ 新建章节"], key="struct_ch")
        if sel_ch == "➕ 新建章节":
            new_ch = st.selectbox("新建章节：", [c for c in all_chapters if c not in built], key="new_ch")
            if st.button("✅ 创建章节", key="create_ch_btn") and new_ch:
                structure[new_ch] = {"framework_image": "", "sections": []}
                save_json(structure, CHAPTER_STRUCTURE_FILE); st.success(f"✅ 章节「{new_ch}」已创建"); st.rerun()
            st.stop()
        ch_data = structure[sel_ch]

        st.markdown("### 📊 本章框架图")
        if ch_data.get("framework_image"):
            try: st.image(ch_data["framework_image"], width=500)
            except: st.info("📊 框架图缺失")
        uploaded_img = st.file_uploader("上传框架图（图片或Word）", type=["png","jpg","jpeg","docx"], key="fw_upload")
        if uploaded_img:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); img_path = os.path.join(UPLOAD_DIR, f"fw_{ts}_{uploaded_img.name}")
            with open(img_path, "wb") as f: f.write(uploaded_img.getbuffer())
            ch_data["framework_image"] = img_path; save_json(structure, CHAPTER_STRUCTURE_FILE); st.success("✅ 框架图已上传"); st.rerun()

        st.markdown("---"); st.markdown("### 📑 小节管理")
        sections = ch_data.get("sections", [])
        new_sec_title = st.text_input("新小节标题：", placeholder="例如：§1.1 电磁辐射原理", key="new_sec_title")
        if st.button("➕ 添加小节", key="add_section_btn") and new_sec_title:
            sections.append({"title": new_sec_title, "knowledge_points": []})
            ch_data["sections"] = sections; save_json(structure, CHAPTER_STRUCTURE_FILE); st.success(f"✅ 小节「{new_sec_title}」已添加"); st.rerun()

        for i, sec in enumerate(sections):
            st.markdown("---"); st.markdown(f"### 📖 {sec['title']}（{len(sec.get('knowledge_points', []))}个知识点）")
            kps = sec.get("knowledge_points", [])

            st.markdown("#### ➕ 添加新知识点")
            kp_title_input = st.text_input("知识点标题：", key=f"new_kp_title_{i}", placeholder="例如：电磁辐射基本概念")
            if st.button(f"🔍 检索课本内容", key=f"new_kp_search_{i}") and kp_title_input:
                with st.spinner("正在检索并整理课本内容..."): textbook = search_and_format_textbook(kp_title_input, chapter_filter=sel_ch, n_results=4)
                st.session_state[f"new_kp_textbook_{i}"] = textbook; st.session_state[f"new_kp_title_val_{i}"] = kp_title_input
                st.session_state[f"new_think_{i}"] = ""; st.session_state[f"new_kp_img_list_{i}"] = []; st.session_state[f"new_kp_tb_img_list_{i}"] = []
                st.rerun()

            if f"new_kp_title_val_{i}" in st.session_state:
                kp_title_val = st.session_state[f"new_kp_title_val_{i}"]; textbook = st.session_state.get(f"new_kp_textbook_{i}", "")
                st.markdown(f"**正在添加：{kp_title_val}**")
                st.markdown("##### 📖 课本内容（可编辑）")
                edited_textbook = st.text_area("课本内容：", textbook, height=200, key=f"new_kp_tb_edit_{i}", label_visibility="collapsed")

                st.markdown("##### 🖼️ 课本配图（可多选）")
                if f"new_kp_tb_img_list_{i}" not in st.session_state: st.session_state[f"new_kp_tb_img_list_{i}"] = []
                tb_multi = st.file_uploader("选择课本配图（可多选）", type=["png","jpg","jpeg"], accept_multiple_files=True, key=f"tb_multi_{i}")
                if tb_multi and not st.session_state.get(f"tb_done_{i}", False):
                    st.session_state[f"tb_done_{i}"] = True
                    for uf in tb_multi:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f"); ip = os.path.join(UPLOAD_DIR, f"tbimg_{ts}_{uf.name}")
                        with open(ip, "wb") as f: f.write(uf.getbuffer()); st.session_state[f"new_kp_tb_img_list_{i}"].append(ip)
                    st.rerun()
                tb_list = st.session_state[f"new_kp_tb_img_list_{i}"]
                if tb_list:
                    st.caption(f"已上传 {len(tb_list)} 张课本配图：")
                    cols = st.columns(4)
                    for idx, p in enumerate(tb_list):
                        with cols[idx % 4]:
                            if os.path.exists(p):
                                try: st.image(p, width=100)
                                except: st.caption("图片已损坏")
                                if st.button("🗑️", key=f"dtb_{i}_{idx}"): st.session_state[f"new_kp_tb_img_list_{i}"].pop(idx); st.rerun()

                st.markdown("##### 🤔 思考题")
                if f"new_think_{i}" not in st.session_state: st.session_state[f"new_think_{i}"] = ""
                if st.button("🎲 自动生成思考题", key=f"gen_think_btn_{i}", use_container_width=True):
                    with st.spinner("生成中..."): st.session_state[f"new_think_{i}"] = generate_think_question(kp_title_val, edited_textbook, sel_ch)
                    st.rerun()
                think_input = st.text_area("思考题：", key=f"new_think_{i}", placeholder="点击上方按钮自动生成，或手动输入")

                st.markdown("##### 🌰 生活实例（可选）")
                life_text = st.text_area("实例文字说明：", key=f"new_kp_life_{i}", height=68, placeholder="例如：微波炉加热食物就是电磁辐射在日常生活中的应用...")

                st.markdown("##### 📷 生活实例配图（可多选）")
                if f"new_kp_img_list_{i}" not in st.session_state: st.session_state[f"new_kp_img_list_{i}"] = []
                img_multi = st.file_uploader("选择图片（可多选）", type=["png","jpg","jpeg"], accept_multiple_files=True, key=f"img_multi_{i}")
                if img_multi and not st.session_state.get(f"img_done_{i}", False):
                    st.session_state[f"img_done_{i}"] = True
                    for uf in img_multi:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f"); ip = os.path.join(UPLOAD_DIR, f"ex_{ts}_{uf.name}")
                        with open(ip, "wb") as f: f.write(uf.getbuffer()); st.session_state[f"new_kp_img_list_{i}"].append(ip)
                    st.rerun()
                img_list = st.session_state[f"new_kp_img_list_{i}"]
                if img_list:
                    st.caption(f"已上传 {len(img_list)} 张图片：")
                    cols = st.columns(4)
                    for idx, p in enumerate(img_list):
                        with cols[idx % 4]:
                            if os.path.exists(p):
                                try: st.image(p, width=100)
                                except: st.caption("图片已损坏")
                                if st.button("🗑️", key=f"di_{i}_{idx}"): st.session_state[f"new_kp_img_list_{i}"].pop(idx); st.rerun()

                col_vid = st.columns([1])[0]
                with col_vid: uploaded_video_ex = st.file_uploader("🎬 视频（可选）：", type=["mp4","avi","mov"], key=f"new_kp_vid_{i}")
                video_path_ex = ""
                if uploaded_video_ex is not None:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S"); video_path_ex = os.path.join(UPLOAD_DIR, f"ex_v_{ts}_{uploaded_video_ex.name}")
                    with open(video_path_ex, "wb") as f: f.write(uploaded_video_ex.getbuffer()); st.success("✅ 视频已上传")

                if st.button(f"✅ 确认添加知识点", key=f"new_kp_confirm_{i}", type="primary"):
                    examples = []
                    if life_text.strip(): examples.append({"type":"text","content":life_text.strip()})
                    for ip in st.session_state.get(f"new_kp_img_list_{i}", []): examples.append({"type":"image","content":ip})
                    if video_path_ex: examples.append({"type":"video","content":video_path_ex})
                    final_think = st.session_state.get(f"new_think_{i}", think_input)
                    tb_imgs = st.session_state.get(f"new_kp_tb_img_list_{i}", [])
                    kps.append({"title":kp_title_val,"textbook_content":edited_textbook,"textbook_images":tb_imgs,"selected_examples":examples,"think_question":final_think})
                    save_json(structure, CHAPTER_STRUCTURE_FILE)
                    for k in [f"new_kp_title_val_{i}",f"new_kp_textbook_{i}",f"new_think_{i}",f"new_kp_img_list_{i}",f"new_kp_tb_img_list_{i}"]: st.session_state.pop(k, None)
                    st.success(f"✅ 知识点「{kp_title_val}」已添加！"); st.rerun()

            if kps:
                st.markdown("---"); st.markdown("#### 📌 已有知识点")
                for j, kp in enumerate(kps):
                    with st.expander(f"📌 {j+1}. {kp['title']}", expanded=False):
                        edit_title = st.text_input("知识点标题：", kp['title'], key=f"edit_title_{i}_{j}")
                        st.markdown("##### 📖 课本内容")
                        edit_textbook = st.text_area("课本内容：", kp.get('textbook_content',''), height=150, key=f"edit_tb_{i}_{j}", label_visibility="collapsed")
                        if st.button(f"🔍 重新检索课本", key=f"re_search_{i}_{j}"):
                            with st.spinner("检索中..."): result = search_and_format_textbook(edit_title or kp['title'], chapter_filter=sel_ch, n_results=4)
                            st.session_state[f"re_result_{i}_{j}"] = result; st.rerun()
                        if f"re_result_{i}_{j}" in st.session_state:
                            st.markdown("📖 **新检索结果：**"); st.text(st.session_state[f"re_result_{i}_{j}"])
                            if st.button("✅ 采用此结果", key=f"use_re_{i}_{j}"):
                                kp['textbook_content'] = st.session_state.pop(f"re_result_{i}_{j}")
                                save_json(structure, CHAPTER_STRUCTURE_FILE); st.success("✅ 已更新"); st.rerun()

                        st.markdown("##### 🖼️ 课本配图")
                        tb_images = kp.get('textbook_images', []) or ([kp['textbook_image']] if kp.get('textbook_image') else [])
                        if tb_images:
                            st.caption(f"共 {len(tb_images)} 张课本配图：")
                            cols = st.columns(4)
                            for idx, img_path in enumerate(tb_images):
                                with cols[idx % 4]:
                                    if os.path.exists(img_path):
                                        try: st.image(img_path, width=100)
                                        except: st.caption("图片已损坏")
                                        if st.button("🗑️", key=f"del_edit_tb_img_{i}_{j}_{idx}"): tb_images.pop(idx); kp['textbook_images'] = tb_images; save_json(structure, CHAPTER_STRUCTURE_FILE); st.rerun()
                        edit_tb_multi = st.file_uploader("补充课本配图（可多选）", type=["png","jpg","jpeg"], accept_multiple_files=True, key=f"edit_tb_multi_{i}_{j}")
                        if edit_tb_multi and not st.session_state.get(f"edit_tb_done_{i}_{j}", False):
                            st.session_state[f"edit_tb_done_{i}_{j}"] = True
                            for uf in edit_tb_multi:
                                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f"); ip = os.path.join(UPLOAD_DIR, f"tbimg_{ts}_{uf.name}")
                                with open(ip, "wb") as f: f.write(uf.getbuffer()); tb_images.append(ip)
                            kp['textbook_images'] = tb_images; save_json(structure, CHAPTER_STRUCTURE_FILE); st.rerun()

                        st.markdown("##### 🤔 思考题")
                        if f"edit_think_{i}_{j}" not in st.session_state: st.session_state[f"edit_think_{i}_{j}"] = kp.get('think_question','')
                        if st.button("🎲 重新生成思考题", key=f"re_gen_btn_{i}_{j}", use_container_width=True):
                            with st.spinner("生成中..."): st.session_state[f"edit_think_{i}_{j}"] = generate_think_question(edit_title or kp['title'], edit_textbook, sel_ch)
                            st.rerun()
                        edit_think_input = st.text_area("思考题：", key=f"edit_think_{i}_{j}", placeholder="点击上方按钮重新生成")

                        st.markdown("##### 🌰 生活实例")
                        current_examples = kp.get('selected_examples', [])
                        existing_text = "\n".join(e.get('content','') if isinstance(e,dict) else e for e in current_examples if not (isinstance(e,dict) and e.get('type') in ('image','video')))
                        edit_life = st.text_area("实例文字：", existing_text, height=68, key=f"edit_life_{i}_{j}")

                        st.markdown("##### 📷 生活实例配图")
                        life_images = [e for e in current_examples if isinstance(e,dict) and e.get('type')=='image']
                        if life_images:
                            st.caption(f"共 {len(life_images)} 张实例配图：")
                            cols = st.columns(4)
                            for idx, e in enumerate(life_images):
                                with cols[idx % 4]:
                                    if os.path.exists(e['content']):
                                        try: st.image(e['content'], width=100)
                                        except: st.caption("图片已损坏")
                                        if st.button("🗑️", key=f"del_edit_life_img_{i}_{j}_{idx}"): current_examples.remove(e); save_json(structure, CHAPTER_STRUCTURE_FILE); st.rerun()

                        for e in current_examples:
                            if isinstance(e,dict) and e.get('type')=='video' and e.get('content') and os.path.exists(e['content']): st.video(e['content'])
                        col_eimg, col_evid = st.columns([1,1])
                        with col_eimg:
                            edit_img_multi = st.file_uploader("补充图片（可多选）", type=["png","jpg","jpeg"], accept_multiple_files=True, key=f"edit_img_multi_{i}_{j}")
                            if edit_img_multi and not st.session_state.get(f"edit_img_done_{i}_{j}", False):
                                st.session_state[f"edit_img_done_{i}_{j}"] = True
                                for uf in edit_img_multi:
                                    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f"); ip = os.path.join(UPLOAD_DIR, f"edit_{ts}_{uf.name}")
                                    with open(ip, "wb") as f: f.write(uf.getbuffer()); current_examples.append({"type":"image","content":ip})
                                save_json(structure, CHAPTER_STRUCTURE_FILE); st.rerun()
                        with col_evid:
                            edit_vid = st.file_uploader("补充视频：", type=["mp4","avi","mov"], key=f"edit_vid_{i}_{j}")
                            if edit_vid is not None:
                                ts = datetime.now().strftime("%Y%m%d_%H%M%S"); vp = os.path.join(UPLOAD_DIR, f"edit_{ts}_{edit_vid.name}")
                                with open(vp, "wb") as f: f.write(edit_vid.getbuffer()); current_examples.append({"type":"video","content":vp})
                                save_json(structure, CHAPTER_STRUCTURE_FILE); st.success("✅ 视频已添加")

                        col_save, col_del = st.columns([2,1])
                        with col_save:
                            if st.button("💾 保存修改", key=f"save_kp_{i}_{j}"):
                                examples = []
                                if edit_life.strip():
                                    for line in edit_life.strip().split("\n"):
                                        if line.strip(): examples.append({"type":"text","content":line.strip()})
                                for e in current_examples:
                                    if isinstance(e,dict) and e.get('type') in ('image','video') and e.get('content'): examples.append(e)
                                final_think = st.session_state.get(f"edit_think_{i}_{j}", edit_think_input)
                                kp.update({"title":edit_title,"textbook_content":st.session_state.get(f"edit_tb_{i}_{j}",edit_textbook),"textbook_images":tb_images,"selected_examples":examples,"think_question":final_think})
                                save_json(structure, CHAPTER_STRUCTURE_FILE); st.success("✅ 已保存"); st.rerun()
                        with col_del:
                            if st.button("🗑️ 删除", key=f"del_kp_{i}_{j}"): kps.pop(j); save_json(structure, CHAPTER_STRUCTURE_FILE); st.success("🗑️ 已删除"); st.rerun()

            if st.button(f"🗑️ 删除本节「{sec['title']}」", key=f"del_sec_{i}"):
                sections.pop(i); ch_data["sections"] = sections; save_json(structure, CHAPTER_STRUCTURE_FILE); st.success("🗑️ 小节已删除"); st.rerun()

    with tabs[1]:
        st.subheader("📋 课堂问答管理")
        class_qa = load_json(CLASS_QA_FILE)
        if not isinstance(class_qa, list): class_qa = []
        qa_chapter = st.selectbox("选择章节：", all_chapters, key="class_qa_ch")
        qa_question_type = st.selectbox("题目类型：", ["简答题","选择题","填空题"], key="class_qa_qtype")
        qa_type = st.radio("检测类型：", ["课前预习检测","课中随堂检测"], key="class_qa_type")
        qa_knowledge = st.text_input("关联知识点（用于AI出题）：", key="class_qa_knowledge", placeholder="例如：普朗克辐射定律")
        qa_question = st.text_area("输入问题：", key="class_qa_input", placeholder="输入问题，或点击下方按钮自动生成")

        if st.button("🎲 自动生成问题", key="class_qa_gen"):
            with st.spinner("生成中..."):
                ctx = "\n".join(search_textbook(qa_knowledge if qa_knowledge else f"{qa_chapter} 核心知识点", chapter_filter=qa_chapter, n_results=5))
                if qa_question_type == "选择题":
                    sp = f"围绕「{qa_knowledge or qa_chapter}」出1道单选题。输出格式：\n【题目】xxx\nA. xxx  B. xxx  C. xxx  D. xxx\n【答案】X\n【解析】xxx"
                elif qa_question_type == "填空题":
                    sp = f"围绕「{qa_knowledge or qa_chapter}」出1道填空题，用______标记空白处。输出格式：\n【题目】xxx\n【答案】xxx"
                else:
                    sp = f"围绕「{qa_knowledge or qa_chapter}」出1道简答题。输出格式：\n【题目】xxx\n【答案要点】xxx"
                if qa_type == "课中随堂检测": sp += "要求：难度适中偏难，考察理解和应用。"
                result = call_llm(sp, f"课本内容：\n{ctx}", max_tokens=500)
                q_part = result.split("【题目】")[1] if "【题目】" in result else result
                q_text = q_part.split("【答案】")[0].split("【答案要点】")[0].strip()
                a_text = q_part.split("【答案】")[1].split("【解析】")[0].strip() if "【答案】" in q_part else (q_part.split("【答案要点】")[1].strip() if "【答案要点】" in q_part else "")
                st.session_state["class_qa_generated_q"] = q_text; st.session_state["class_qa_generated_a"] = a_text
                st.rerun()

        if "class_qa_generated_q" in st.session_state: qa_question = st.text_area("输入问题（可编辑）：", value=st.session_state["class_qa_generated_q"], key="class_qa_input_q_edit")
        if "class_qa_generated_a" in st.session_state: ref_answer = st.text_area("参考答案：", value=st.session_state["class_qa_generated_a"], key="class_qa_input_a_edit")
        else: ref_answer = st.text_area("参考答案（可选）：", key="class_qa_ref", placeholder="可留空")

        deadline_date = st.date_input("截止日期：", key="class_qa_deadline_date")
        deadline_time = st.time_input("截止时间：", key="class_qa_deadline_time")
        deadline = f"{deadline_date} {deadline_time.strftime('%H:%M')}" if deadline_date else ""

        if st.button("📤 发布问题", key="class_qa_publish"):
            final_q = qa_question or st.session_state.get("class_qa_generated_q", "")
            final_a = ref_answer or st.session_state.get("class_qa_generated_a", "")
            if final_q:
                class_qa.append({"chapter":qa_chapter,"type":qa_type,"question":final_q,"q_type":qa_question_type,"reference":final_a,"deadline":deadline,"time":datetime.now().strftime("%Y-%m-%d %H:%M")})
                save_json(class_qa, CLASS_QA_FILE); st.success(f"✅ 已发布：{qa_type}（{qa_question_type}）- {final_q[:50]}...")
                for k in ["class_qa_generated","class_qa_generated_q","class_qa_generated_a"]:
                    if k in st.session_state: st.session_state.pop(k)
                st.rerun()
            else: st.warning("请输入问题")

        if class_qa:
            st.markdown("---"); st.markdown("### 📋 已发布问题")
            all_qa_chapters = list(set(item.get('chapter','未知') for item in class_qa))
            selected_qa_chapter = st.selectbox("筛选章节：", ["全部"] + all_qa_chapters, key="qa_filter_chapter")
            filtered_qa = class_qa if selected_qa_chapter == "全部" else [item for item in class_qa if item.get('chapter') == selected_qa_chapter]
            chapter_dict = {}
            for item in filtered_qa:
                ch = item.get('chapter','未知'); tp = item.get('type','')
                chapter_dict.setdefault(ch,{}).setdefault(tp,[]).append(item)
            for ch, types in chapter_dict.items():
                st.markdown(f"#### 📚 {ch}")
                for tp, items in types.items():
                    icon = "📖" if tp == "课前预习检测" else "📝"
                    st.markdown(f"**{icon} {tp}**（{len(items)}题）")
                    for idx, item in enumerate(items):
                        with st.expander(f"{item.get('q_type','')} | {item.get('time','')} | {item['question'][:40]}..."):
                            st.write(f"**问题**：{item['question']}")
                            if item.get('reference'): st.write(f"**参考答案**：{item['reference']}")
                            if item.get('deadline'): st.write(f"⏰ 截止：{item['deadline']}")
                            if st.button("🗑️ 删除", key=f"del_qa_{item.get('time','')}_{idx}"): class_qa.remove(item); save_json(class_qa, CLASS_QA_FILE); st.rerun()

    with tabs[2]:
        st.subheader("发布章节检测")
        sel = st.selectbox("章节：", all_chapters, key="quiz_ch")
        st.markdown("#### 设置各题型数量")
        col1, col2, col3 = st.columns(3)
        with col1: choice_count = st.number_input("选择题数量", min_value=0, max_value=10, value=2, key="quiz_choice")
        with col2: fill_count = st.number_input("填空题数量", min_value=0, max_value=10, value=1, key="quiz_fill")
        with col3: essay_count = st.number_input("简答题数量", min_value=0, max_value=10, value=1, key="quiz_essay")
        total_count = choice_count + fill_count + essay_count
        if total_count == 0: st.warning("请至少设置1道题")
        else:
            if st.button("🎲 生成章节检测", key="gen_quiz_btn"):
                all_questions = []
                with st.spinner(f"正在生成 {total_count} 道题目..."):
                    for qtype, cnt in [("选择题",choice_count),("填空题",fill_count),("简答题",essay_count)]:
                        if cnt > 0:
                            qs = generate_quiz_questions(sel, qtype, cnt)
                            if qs and "题目" in qs: all_questions.append((qtype, qs))
                st.session_state["gen_quiz_mixed"] = all_questions; st.rerun()

        if "gen_quiz_mixed" in st.session_state:
            st.markdown("---"); st.markdown("### 生成的题目（可编辑）")
            all_questions = st.session_state["gen_quiz_mixed"]; edited_all = []
            for q_type, raw_text in all_questions:
                st.markdown(f"#### {q_type}")
                edited_text = st.text_area(f"{q_type}题目：", raw_text, height=200, key=f"quiz_edit_{q_type}")
                edited_all.append((q_type, edited_text))
            if st.button("📤 发布章节检测", key="publish_quiz_btn"):
                quizzes = load_json(QUIZ_FILE)
                if not isinstance(quizzes, list): quizzes = []
                questions = []
                for q_type, text in edited_all:
                    for block in text.split("题目")[1:]:
                        lines = block.strip().split("\n")
                        if lines:
                            first_line = lines[0]
                            q_text = first_line.split("：",1)[-1].strip() if "：" in first_line else first_line.strip()
                            full_question = block.strip()
                            clean_lines = []
                            for line in full_question.split("\n"):
                                if line.startswith("答案：") or line.startswith("正确选项内容：") or line.startswith("解析：") or line.startswith("参考答案要点：") or line.startswith("参考答案"): break
                                clean_lines.append(line)
                            full_question = "\n".join(clean_lines).strip()
                            ans = ""
                            for line in lines:
                                if "答案：" in line: ans = line.split("答案：")[-1].strip()
                                elif "正确选项内容：" in line: ans = line.split("正确选项内容：")[-1].strip()
                                elif "参考答案要点：" in line: ans = line.split("参考答案要点：")[-1].strip()
                            if q_text: questions.append({"question":full_question,"answer":ans or "教师未提供参考答案","q_type":q_type})
                if questions:
                    quizzes.append({"chapter":sel,"q_type":"混合","questions":questions})
                    save_json(quizzes, QUIZ_FILE); st.success(f"✅ 发布 {len(questions)} 道题（选择{choice_count}/填空{fill_count}/简答{essay_count}）")
                    del st.session_state["gen_quiz_mixed"]; st.rerun()
                else: st.error("题目解析失败，请检查格式")

        quizzes = load_json(QUIZ_FILE)
        if isinstance(quizzes, list) and quizzes:
            st.markdown("---"); st.markdown("### 📋 已发布检测")
            for idx, qz in enumerate(quizzes):
                with st.expander(f"{qz['chapter']}（{qz.get('q_type','混合')}，{len(qz['questions'])}题）"):
                    for i, qi in enumerate(qz['questions']): st.write(f"**{i+1}**（{qi.get('q_type','')}）{qi['question']}")
                    if st.button(f"🗑️ 删除此检测", key=f"del_quiz_{idx}"): quizzes.pop(idx); save_json(quizzes, QUIZ_FILE); st.success("🗑️ 已删除"); st.rerun()

    with tabs[3]:
        st.subheader("论文推荐")
        sel = st.selectbox("章节：", all_chapters, key="paper_ch")
        paper_mode = st.radio("添加方式：", ["手动输入","上传文件"], key="paper_mode", horizontal=True)
        if paper_mode == "手动输入":
            title = st.text_input("标题", key="paper_title")
            author = st.text_input("作者", key="paper_author")
            abstract = st.text_area("摘要", key="paper_abstract")
            if st.button("➕ 添加", key="add_paper_btn") and title:
                papers = load_json(PAPER_FILE)
                if not isinstance(papers, list): papers = []
                papers.append({"chapter":sel,"title":title,"author":author,"abstract":abstract}); save_json(papers, PAPER_FILE); st.success("✅ 已添加"); st.rerun()
        else:
            uploaded_papers = st.file_uploader("上传论文文件（可多选）", type=["pdf","docx","doc","txt"], accept_multiple_files=True, key="paper_files_upload")
            if uploaded_papers:
                papers = load_json(PAPER_FILE)
                if not isinstance(papers, list): papers = []
                existing_names = {p.get("title","") for p in papers if p.get("chapter")==sel}
                paper_dir = os.path.join(UPLOAD_DIR, "papers")
                os.makedirs(paper_dir, exist_ok=True)
                added = 0
                for up in uploaded_papers:
                    if up.name in existing_names: continue
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S"); file_path = os.path.join(paper_dir, f"{ts}_{up.name}")
                    with open(file_path, "wb") as f: f.write(up.getbuffer())
                    papers.append({"chapter":sel,"title":up.name,"author":"","abstract":"","file_path":file_path,"is_file":True}); added += 1
                save_json(papers, PAPER_FILE)
                if added > 0: st.success(f"✅ 已上传 {added} 篇论文"); st.rerun()
        papers = load_json(PAPER_FILE)
        if isinstance(papers, list) and papers:
            st.markdown("---"); st.markdown("### 📄 已推荐论文")
            for idx, p in enumerate(papers):
                if p.get("chapter") != sel: continue
                if p.get("is_file"):
                    with st.expander(f"📄 {p['title']}"):
                        if os.path.exists(p.get('file_path','')):
                            with open(p['file_path'],"rb") as f: st.download_button("📥 下载论文", f.read(), file_name=p['title'], key=f"dl_paper_{idx}")
                        if st.button("🗑️ 删除", key=f"del_paper_{idx}"): papers.pop(idx); save_json(papers, PAPER_FILE); st.rerun()
                else:
                    with st.expander(f"📄 {p['title']}"):
                        st.write(f"**作者**：{p.get('author','')}"); st.write(f"**摘要**：{p.get('abstract','')}")
                        if st.button("🗑️ 删除", key=f"del_paper_{idx}"): papers.pop(idx); save_json(papers, PAPER_FILE); st.rerun()

    with tabs[4]:
        st.subheader("🔬 ENVI 实例上传")
        sel = st.selectbox("章节：", all_chapters, key="case_ch")
        if st.button("📤 上传", key="upload_case_btn") and (ct := st.text_input("标题", key="case_title")) and (ud := st.file_uploader("上传文档（.txt）或视频（mp4/avi/mov）", type=["txt","mp4","avi","mov"], key="case_doc")):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S"); fp = os.path.join(UPLOAD_DIR, f"case_{ts}_{ud.name}")
            with open(fp, "wb") as f: f.write(ud.getbuffer())
            is_vid = ud.name.lower().endswith(('.mp4','.avi','.mov')); cnt = "" if is_vid else ud.read().decode("utf-8")
            cases = load_json(CASE_FILE)
            if not isinstance(cases, list): cases = []
            cases.append({"chapter":sel,"title":ct,"content":cnt,"file_path":fp,"is_video":is_vid,"upload_time":datetime.now().strftime("%Y-%m-%d %H:%M")})
            save_json(cases, CASE_FILE); st.success("✅ 已上传"); st.rerun()

    with tabs[5]:
        st.subheader("学生操作问题")
        qa = analyze_logs_by_type("qa"); unresolved = [r for r in qa if not r.get("resolved",False)]
        st.metric("待处理", len(unresolved))
        for i, r in enumerate(unresolved):
            with st.expander(f"🔴 {r['student_name']} - {r['timestamp'][:19]}"):
                st.write(r['question'])
                if r.get("screenshot") and os.path.exists(r["screenshot"]): st.image(r["screenshot"])
                if st.button("📤 发送", key=f"send_{i}") and (rp := st.text_area("回复：", key=f"reply_{i}")):
                    r["teacher_reply"] = rp; r["resolved"] = True
                    log_interaction("qa_reply", {"original_ts":r["timestamp"],"student_id":r["student_id"],"teacher_reply":rp}); st.success("✅"); st.rerun()


    with tabs[6]:
        st.subheader("📊 学情总览")
        structure = load_json(CHAPTER_STRUCTURE_FILE)
        view_ch = st.selectbox("选择章节：", list(structure.keys()), key="overview_ch")
        ch_data = structure.get(view_ch, {})
        total_kps = sum(len(sec.get("knowledge_points",[])) for sec in ch_data.get("sections",[]))
        filter_col1, _ = st.columns([1,3])
        with filter_col1: show_mode = st.selectbox("筛选：", ["全部","仅显示错题","仅显示未做"], key="overview_filter")
        tab1, tab2, tab3 = st.tabs(["📈 学习进度与思考题","📋 课堂问答","📝 章节检测"])

        with tab1:
            study_log = load_json(STUDY_LOG_FILE); students = study_log.get("students",{})
            all_kps = []
            for sec in ch_data.get("sections",[]):
                for kp in sec.get("knowledge_points",[]): all_kps.append(kp)
            if not all_kps: st.info("该章节暂无知识点")
            else:
                for sk, chs in students.items():
                    parts = sk.split("_",1); si = parts[0] if len(parts)>0 else ""; sn = parts[1] if len(parts)>1 else sk
                    learned = set(chs.get(view_ch,{}).get("learned",[])); progress = len(learned)
                    with st.expander(f"📖 {si}{sn}（进度 {progress}/{total_kps}）"):
                        if st.button(f"🗑️ 删除{si}{sn}的所有数据", key=f"del_prog_{sk}"):
                            if sk in students: del students[sk]; save_json(study_log, STUDY_LOG_FILE)
                            all_lines = []
                            if os.path.exists(LOG_FILE):
                                with open(LOG_FILE,"r",encoding="utf-8") as f: all_lines = f.readlines()
                                with open(LOG_FILE,"w",encoding="utf-8") as f:
                                    for line in all_lines:
                                        try:
                                            r = json.loads(line)
                                            if f"{r.get('student_id','')}_{r.get('student_name','')}" != sk: f.write(line)
                                        except: f.write(line)
                            st.success(f"✅ {si}{sn}的数据已删除"); st.rerun()
                        st.progress(min(max(progress/total_kps,0.0),1.0) if total_kps>0 else 0.0)
                        for kp_idx, kp in enumerate(all_kps):
                            kp_title = kp.get('title',f'知识点{kp_idx+1}'); think_q = kp.get('think_question','')
                            student_think = [r for r in analyze_logs_by_type("think")
                                             if r.get('chapter')==view_ch and f"{r.get('student_id','')}_{r.get('student_name','')}"==sk and r.get('kp_title')==kp_title]
                            if not think_q: status_color, status_text = "#e0e0e0","—"
                            elif not student_think: status_color, status_text = "#9e9e9e","未做"
                            else:
                                score = student_think[-1].get('score',0)
                                if score>=80: status_color, status_text = "#4caf50","✓"
                                elif score>=40: status_color, status_text = "#ff9800","△"
                                else: status_color, status_text = "#f44336","✗"
                            if show_mode=="仅显示错题" and status_color not in ["#f44336","#ff9800"]: continue
                            if show_mode=="仅显示未做" and status_color!="#9e9e9e": continue
                            col_kp, col_status = st.columns([5,1])
                            with col_kp:
                                st.write(f"**{kp_title}**")
                                if think_q: st.caption(f"思考题：{think_q[:50]}...")
                            with col_status: st.markdown(f"<div style='background:{status_color};color:white;text-align:center;border-radius:20px;padding:4px 12px;font-size:0.9em'>{status_text}</div>",unsafe_allow_html=True)
                            if think_q and student_think:
                                r = student_think[-1]
                                if st.button(f"查看详情", key=f"think_detail_{sk}_{kp_idx}"):
                                    st.session_state[f"think_detail_open_{sk}_{kp_idx}"] = not st.session_state.get(f"think_detail_open_{sk}_{kp_idx}",False)
                                if st.session_state.get(f"think_detail_open_{sk}_{kp_idx}",False):
                                    st.write(f"**思考题**：{think_q}"); st.write(f"**学生回答**：{r.get('student_answer','')}"); st.caption(r.get('evaluation','')); st.markdown("---")

        with tab2:
            cqr = [r for r in analyze_logs_by_type("class_qa") if r.get("chapter")==view_ch]
            if not cqr: st.info("暂无课堂问答记录")
            else:
                pre = [r for r in cqr if r.get('type')=='课前预习检测']; mid = [r for r in cqr if r.get('type')=='课中随堂检测']
                qa_tab1, qa_tab2 = st.tabs(["📖 课前预习检测","📝 课中随堂检测"])
                for qa_tab, data, label in [(qa_tab1, pre, "课前预习"), (qa_tab2, mid, "课中随堂")]:
                    with qa_tab:
                        if not data: st.info(f"暂无{label}检测记录")
                        else:
                            stu_dict = {}
                            for r in data: k = f"{r['student_id']}{r['student_name']}"; stu_dict.setdefault(k,[]).append(r)
                            for stu, recs in stu_dict.items():
                                correct = sum(1 for r in recs if r.get('score',0)>=80)
                                with st.expander(f"{stu} | 共{len(recs)}题 | 正确{correct}"):
                                    if st.button(f"🗑️ 删除{stu}的{label}问答", key=f"del_cqa_{label}_{stu}"):
                                        all_lines = []
                                        if os.path.exists(LOG_FILE):
                                            with open(LOG_FILE,"r",encoding="utf-8") as f: all_lines = f.readlines()
                                            with open(LOG_FILE,"w",encoding="utf-8") as f:
                                                for line in all_lines:
                                                    try:
                                                        r = json.loads(line)
                                                        if not (f"{r.get('student_id','')}{r.get('student_name','')}"==stu and r.get('log_type')=='class_qa' and r.get('type')==('课前预习检测' if label=="课前预习" else "课中随堂检测")): f.write(line)
                                                    except: f.write(line)
                                        st.success(f"✅ {stu}的{label}问答已删除"); st.rerun()
                                    for r in recs:
                                        score = r.get('score',0); icon = "✅" if score>=80 else "❌"
                                        q_type = r.get('q_type',''); question = r.get('question','')
                                        st.markdown(f"{icon} **{q_type}**")
                                        if q_type=="选择题":
                                            for line in question.split("\n"):
                                                stripped = line.strip()
                                                if stripped.startswith(("A.","B.","C.","D.")): st.write(stripped)
                                                else: st.write(stripped)
                                        else: st.write(question[:80])
                                        st.write(f"**回答**：{r.get('student_answer','')[:80]}")
                                        ref = r.get('reference','') or r.get('evaluation','')
                                        if ref: st.write(f"**参考答案**：{ref[:80]}")
                                        st.caption(f"评分：{score}分"); st.markdown("---")
                            with st.expander(f"📊 全班总览（{label}）", expanded=True):
                                q_unique = {}
                                for r in data:
                                    q_text = r.get('question','')
                                    if q_text not in q_unique: q_unique[q_text] = {"question":q_text,"q_type":r.get('q_type',''),"answers":[],"reference":r.get('reference','') or r.get('evaluation','')}
                                    q_unique[q_text]["answers"].append(r)
                                st.write(f"**共 {len(q_unique)} 道{label}题**")
                                for i, (q_text, q_data) in enumerate(q_unique.items()):
                                    total = len(q_data["answers"]); correct = sum(1 for a in q_data["answers"] if a.get('score',0)>=80)
                                    st.write(f"**第{i+1}题**（{q_data['q_type']}）：{q_text[:200]}"); st.write(f"答对：{correct}人 | 答错：{total-correct}人")
                                    st.write(f"**参考答案**：{q_data['reference'][:80] if q_data['reference'] else '无'}")
                                    for a in q_data["answers"]:
                                        icon = "✅" if a.get('score',0)>=80 else "❌"
                                        st.write(f"{icon} {a.get('student_id','')}{a.get('student_name','')}：{a.get('student_answer','')[:60]}")
                                    st.markdown("---")

        with tab3:
            qr = [r for r in analyze_logs_by_type("quiz") if r.get("chapter")==view_ch]
            if not qr: st.info("暂无章节检测记录")
            else:
                stu_dict = {}
                for r in qr: k = f"{r['student_id']}{r['student_name']}"; stu_dict.setdefault(k,[]).append(r)
                for stu, recs in stu_dict.items():
                    total = len(recs); correct = sum(1 for r in recs if r.get('score',0)>=80)
                    with st.expander(f"{stu} | 共{total}题 | ✅{correct} ❌{total-correct}"):
                        if st.button(f"🗑️ 删除{stu}的章节检测", key=f"del_quiz_{stu}"):
                            all_lines = []
                            if os.path.exists(LOG_FILE):
                                with open(LOG_FILE,"r",encoding="utf-8") as f: all_lines = f.readlines()
                                with open(LOG_FILE,"w",encoding="utf-8") as f:
                                    for line in all_lines:
                                        try:
                                            r = json.loads(line)
                                            if not (f"{r.get('student_id','')}{r.get('student_name','')}"==stu and r.get('log_type')=='quiz'): f.write(line)
                                        except: f.write(line)
                            st.success(f"✅ {stu}的章节检测已删除"); st.rerun()
                        for i, r in enumerate(recs):
                            is_correct = r.get('score',0)>=80; icon = "✅" if is_correct else "❌"
                            q_type = r.get('q_type','')
                            st.markdown(f"**{icon} 第{i+1}题（{q_type}）**"); st.write(r['question'][:100])
                            st.write(f"**学生答案**：{r.get('student_answer','')[:100]}")
                            if q_type!='选择题': st.write(f"**参考答案**：{r.get('correct_answer','')[:100]}")
                            st.write(f"**得分**：{r.get('score',0)}分")
                            if q_type!='选择题':
                                new_score = st.slider("修改评分",0,100,r.get('score',0), key=f"qs_{r['timestamp']}")
                                if st.button("💾 保存", key=f"qsave_{r['timestamp']}"):
                                    r['score'] = new_score
                                    all_lines = []
                                    if os.path.exists(LOG_FILE):
                                        with open(LOG_FILE,"r",encoding="utf-8") as f: all_lines = f.readlines()
                                        with open(LOG_FILE,"w",encoding="utf-8") as f:
                                            for line in all_lines:
                                                try:
                                                    record = json.loads(line)
                                                    if record.get('timestamp')==r['timestamp']: record['score']=new_score
                                                    f.write(json.dumps(record,ensure_ascii=False)+"\n")
                                                except: f.write(line)
                                    st.success("✅ 已保存"); st.rerun()
                            st.markdown("---")
                with st.expander("📊 全班总览（章节检测）", expanded=True):
                    q_unique = {}
                    for r in qr:
                        q_text = r.get('question','')
                        if q_text not in q_unique: q_unique[q_text] = {"question":q_text,"q_type":r.get('q_type',''),"answers":[],"correct_answer":r.get('correct_answer','')}
                        q_unique[q_text]["answers"].append(r)
                    st.write(f"**共 {len(q_unique)} 道检测题**")
                    for i, (q_text, q_data) in enumerate(q_unique.items()):
                        total = len(q_data["answers"]); correct = sum(1 for a in q_data["answers"] if a.get('score',0)>=80)
                        st.write(f"**第{i+1}题**（{q_data['q_type']}）：{q_text[:200]}"); st.write(f"答对：{correct}人 | 答错：{total-correct}人")
                        if q_data['q_type']!='选择题': st.write(f"**参考答案**：{q_data['correct_answer'][:80]}")
                        for a in q_data["answers"]:
                            icon = "✅" if a.get('score',0)>=80 else "❌"
                            st.write(f"{icon} {a.get('student_id','')}{a.get('student_name','')}：{a.get('student_answer','')[:60]}")
                        st.markdown("---")

        st.markdown("---")
        col_del1, col_del2, col_del3, col_del4 = st.columns(4)
        with col_del1:
            if st.button("🗑️ 清空学习进度", key="clear_progress"):
                if os.path.exists(STUDY_LOG_FILE): os.remove(STUDY_LOG_FILE); st.success("✅ 学习进度已清空"); st.rerun()
        with col_del2:
            if st.button("🗑️ 清空课堂问答", key="clear_class_qa"):
                all_lines = []
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE,"r",encoding="utf-8") as f: all_lines = f.readlines()
                    with open(LOG_FILE,"w",encoding="utf-8") as f:
                        for line in all_lines:
                            try:
                                r = json.loads(line)
                                if r.get("log_type")!="class_qa": f.write(line)
                            except: f.write(line)
                st.success("✅ 课堂问答已清空"); st.rerun()
        with col_del3:
            if st.button("🗑️ 清空章节检测", key="clear_quiz"):
                all_lines = []
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE,"r",encoding="utf-8") as f: all_lines = f.readlines()
                    with open(LOG_FILE,"w",encoding="utf-8") as f:
                        for line in all_lines:
                            try:
                                r = json.loads(line)
                                if r.get("log_type")!="quiz": f.write(line)
                            except: f.write(line)
                st.success("✅ 章节检测已清空"); st.rerun()
        with col_del4:
            if st.button("🗑️ 清空全部", key="clear_all_logs"):
                if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
                if os.path.exists(STUDY_LOG_FILE): os.remove(STUDY_LOG_FILE)
                st.success("✅ 全部已清空"); st.rerun()
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE,"r",encoding="utf-8") as f: st.download_button("📥 下载日志", f.read(), file_name="full_log.jsonl", key="dl_log")