"""LaTeX 清洗（Markdown 不再在此剥离，改由 theme.markdown_to_html 渲染）。"""
import re


def sanitize(text):
    """清理 LaTeX 残留，转为适合 Markdown 渲染的干净文本。

    Markdown（标题/粗体/代码块/行内码等）不再在此剥离，
    改由 theme.build_bubble → markdown_to_html 渲染为安全 HTML。
    本函数只处理 LaTeX，避免污染 Markdown 语法。
    """
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

    return text
