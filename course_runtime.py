"""课程运行校验：接口错误、题目解析和评分状态。无真实服务调用副作用。"""
import hashlib
import json
import math
import time
from threading import BoundedSemaphore

_AI_GATE = BoundedSemaphore(1)
import re
from datetime import datetime
from zoneinfo import ZoneInfo


class AIRequestFailure(RuntimeError):
    pass


def api_error_message(exc):
    status = getattr(exc, 'status_code', None)
    body = getattr(exc, 'body', None)
    error = body.get('error', body) if isinstance(body, dict) else {}
    code = error.get('code') if isinstance(error, dict) else None
    code = str(code) if code is not None else ''
    if not re.fullmatch(r'[\w.-]{1,32}', code):
        code = ''
    detail = f'HTTP {status}' if status else type(exc).__name__
    if code:
        detail += f'，服务代码 {code}'
    hints = {400:'请求参数或模型名称不被服务接受，请检查模型配置。',
             401:'AI 密钥未通过认证，请检查云端 ai 配置。',
             402:'服务账户额度不足，请检查服务账户。',
             403:'当前密钥或账户无权调用该模型。',
             404:'模型或接口地址不存在，请检查 ai.model 和 ai.base_url。',
             429:'服务繁忙或达到调用限额，请稍后重试。'}
    business_hints = {
        '1302': '账户请求速率受限，请减少同时调用并稍后重试。',
        '1305': '智谱当前模型访问量过大，请稍后再试；这不是图片存储配置错误。',
        '1113': '服务账户已欠费，请在服务控制台核查。',
        '1308': '账户已达到使用上限，请等待服务返回的重置时间。',
        '1310': '账户达到每周或每月使用上限，请核查服务控制台。',
    }
    hint = business_hints.get(code, hints.get(status, '服务暂不可用，请稍后重试。'))
    return f'AI 请求未完成（{detail}）：{hint} 原内容和未提交的回答仍保留，可重试。'


def _transient_ai_error(exc):
    status = getattr(exc, 'status_code', None)
    body = getattr(exc, 'body', None)
    error = body.get('error', body) if isinstance(body, dict) else {}
    code = str(error.get('code', '')) if isinstance(error, dict) else ''
    # 不把欠费、周期配额或权限错误当成瞬时拥堵反复调用。
    return (status == 429 and code in {'1302','1303','1305','1312'}) or (status in {500,502,503,504} and not code)


def _retry_delay(exc, fallback):
    headers = getattr(getattr(exc, 'response', None), 'headers', {})
    value = headers.get('retry-after') if hasattr(headers, 'get') else None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds > 8:
            return None
        if math.isfinite(seconds) and seconds >= 0:
            return max(fallback, seconds)
    except (TypeError,ValueError):
        pass
    return fallback


def request_text(client, model, system_prompt, user_content, max_tokens=800):
    # 单进程内串行处理，不同账号/模型的外部全局限额仍由服务商决定。
    if not _AI_GATE.acquire(timeout=15):
        raise AIRequestFailure('当前应用的 AI 请求正在排队，请稍后重试；原内容仍保留。')
    try:
        options = getattr(client, 'with_options', None)
        # 只保留这一层重试，避免与 SDK 的自动重试叠加。
        request_client = options(max_retries=0) if callable(options) else client
        for attempt in range(3):
            try:
                response = request_client.chat.completions.create(
                    model=model, messages=[{'role':'system','content':system_prompt},
                                           {'role':'user','content':user_content}],
                    temperature=0.3, max_tokens=max_tokens,
                )
                break
            except Exception as exc:
                if attempt < 2 and _transient_ai_error(exc):
                    delay = _retry_delay(exc, (2,5)[attempt])
                    if delay is None:
                        raise AIRequestFailure(api_error_message(exc) + ' 服务要求较长冷却时间，本次不继续自动请求。') from None
                    time.sleep(delay)
                    continue
                suffix = f' 已自动重试 {attempt} 次，仍未完成，请稍后再试。' if attempt else ''
                raise AIRequestFailure(api_error_message(exc) + suffix) from None
        choices = getattr(response, 'choices', None)
        text = getattr(getattr(choices[0], 'message', None), 'content', None) if choices else None
        if not isinstance(text, str) or not text.strip():
            raise AIRequestFailure('模型没有返回可用正文，本次未更新内容。请重试；持续发生时检查模型配置和输出长度。')
        return text.strip()
    finally:
        _AI_GATE.release()


def question_id(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def parse_score(text):
    match = re.search(r'【(?:得分|评分)】\s*([+-]?\d+(?:\.\d+)?)\s*(?:分|/\s*100)?', str(text))
    if not match:
        return None
    number = float(match.group(1))
    return (int(number) if number.is_integer() else number) if math.isfinite(number) and 0 <= number <= 100 else None


def numeric_score(record):
    value = record.get('score') if 'score' in record else parse_score(record.get('evaluation',''))
    if isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0 <= value <= 100:
        return value
    return -1


def score_label(record):
    score = numeric_score(record)
    return f'{score:g}分' if score >= 0 else '待教师复核'


def choice_options(question):
    pattern = r'(?:^|\s)([A-D])[.．、：:）)]\s*(.*?)(?=(?:\s+[A-D][.．、：:）)])|$)'
    return {m.group(1):m.group(2).strip() for m in re.finditer(pattern, question, re.S)}


def choice_answer(question, reference):
    reference = str(reference or '').strip()
    match = re.match(r'^(?:【?答案】?\s*[:：]?\s*)?([A-Da-d])(?:\s*$|[.．、：:）)(（\s])', reference)
    if match:
        return match.group(1).upper()
    normalized = re.sub(r'\s+', '', reference)
    matches = [key for key,value in choice_options(question).items() if re.sub(r'\s+', '', value)==normalized]
    return matches[0] if len(matches)==1 else None


def grade_objective(question, reference, answer, q_type):
    if not str(answer or '').strip():
        return None, '回答为空，尚未评分。'
    if q_type == '选择题':
        correct = choice_answer(question, reference)
        if correct is None:
            return None, '参考答案缺失或无法唯一匹配，待教师复核。'
        selected = str(answer).strip().upper()
        return (100,'正确') if selected==correct else (0,'错误')
    if not str(reference or '').strip() or reference=='教师未提供参考答案':
        return None, '教师尚未提供参考答案，待教师复核。'
    if str(answer).strip()==str(reference).strip():
        return 100, '正确'
    return None, f'与参考答案不完全一致，待教师复核；参考答案：{reference}'


def deadline_passed(value, now=None):
    if not value:
        return False
    try:
        deadline = datetime.fromisoformat(str(value))
        current = now or course_now()
        if deadline.tzinfo is not None:
            deadline = deadline.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
        if current.tzinfo is not None:
            current = current.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
        return deadline < current
    except ValueError:
        return False


def parse_quiz_text(text, q_type):
    blocks = re.split(r'(?:^|\n)\s*题目\s*\d*\s*[:：.]?\s*', text)[1:]
    result=[]
    for block in blocks:
        lines=block.strip().splitlines()
        question=[]; answer=''; detail=[]; in_answer=False
        for line in lines:
            match=re.match(r'\s*(答案|正确选项内容|参考答案要点|参考答案|解析)\s*[:：]\s*(.*)',line)
            if match:
                in_answer=True
                if match.group(1)!='解析':
                    if not answer or match.group(1)=='答案': answer=match.group(2).strip()
                    detail=[answer]
            elif in_answer:
                if q_type=='简答题' and line.strip(): detail.append(line.strip())
            else:
                question.append(line)
        stem='\n'.join(question).strip()
        if q_type=='简答题': answer='\n'.join(detail).strip()
        if stem:
            result.append({'question':stem,'answer':answer,'q_type':q_type})
    return result


def score_icon(record):
    score = numeric_score(record)
    return '⏳' if score < 0 else ('✅' if score >= 80 else '❌')


def course_now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))

