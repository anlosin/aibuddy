"""LaTeX / Markdown 文本清洗，转为干净纯文本"""
import re


def sanitize(text):
    """将 LaTeX 和 Markdown 标记转为干净纯文本"""
    # ── LaTeX ──
    text = re.sub(r'\\\[(.*?)\\\]', lambda m: '\n' + m.group(1).strip() + '\n', text, flags=re.DOTALL)
    text = re.sub(r'\\\((.*?)\\\)', r'\1', text)
    while '\\boxed{' in text:
        i = text.index('\\boxed{')
        depth = 0
        for j in range(i + 7, len(text)):
            if text[j] == '{':
                depth += 1
            elif text[j] == '}':
                if depth == 0:
                    text = text[:i] + text[i+7:j] + text[j+1:]
                    break
                depth -= 1
    text = re.sub(r'\\frac\{(.*?)\}\{(.*?)\}', r'(\1)/(\2)', text)
    text = re.sub(r'\\sqrt\{(.*?)\}', r'√(\1)', text)
    text = text.replace(r'\pm', '±').replace(r'\cdot', '·').replace(r'\times', '×')
    text = text.replace(r'\neq', '≠').replace(r'\geq', '≥').replace(r'\leq', '≤')
    text = re.sub(r'\\(left|right)\s*[\(\)\[\]\{\}]?', '', text)
    # 只清理 LaTeX 残留的孤立花括号，不删除 JSON/代码中的花括号
    text = re.sub(r'\\[a-zA-Z]+\{\}', '', text)
    text = re.sub(r'(?<!\w)\{\}(?!\w)', '', text)

    # ── Markdown ──
    # 围栏代码块
    text = re.sub(r'```[\w]*\n(.*?)\n```', r'\n\1\n', text, flags=re.DOTALL)
    # 行内代码
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    # 标题
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 粗体 **text** 或 __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # 斜体 *text* 或 _text_
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'\1', text)
    # 无序列表 * / - / +
    text = re.sub(r'^[-\*\+]\s+', '• ', text, flags=re.MULTILINE)
    # 水平分割线
    text = re.sub(r'^[\-\*\_]{3,}\s*$', '─' * 30, text, flags=re.MULTILINE)
    # 引用 >
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # 链接
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # 删除线 ~~text~~
    text = re.sub(r'~~(.+?)~~', r'\1', text)

    return text
