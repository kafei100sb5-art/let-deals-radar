"""行情观察与评测文章加载及零依赖 Markdown 渲染。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from . import config

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+|[^)\s]+\.html)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


@dataclass(frozen=True)
class Article:
    """单篇专栏文章元数据与渲染后正文。"""

    slug: str
    title: str
    date: str
    category: str
    summary: str
    body_html: str

    @property
    def href(self) -> str:
        return f"article-{self.slug}.html"


def _format_inline(text: str) -> str:
    """转义 HTML 并渲染行内代码、链接、加粗与斜体。"""
    placeholders: list[str] = []

    def _stash(fragment: str) -> str:
        idx = len(placeholders)
        placeholders.append(fragment)
        return f"\x00PH{idx}\x00"

    def _repl_code(m: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(m.group(1))}</code>")

    def _repl_link(m: re.Match[str]) -> str:
        label = html.escape(m.group(1))
        url = html.escape(m.group(2), quote=True)
        if url.startswith(("http://", "https://")):
            return _stash(
                f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'
            )
        return _stash(f'<a href="{url}">{label}</a>')

    # 先提取行内代码与链接，避免内部星号被误解析
    tmp = _INLINE_CODE_RE.sub(_repl_code, text)
    tmp = _LINK_RE.sub(_repl_link, tmp)
    escaped = html.escape(tmp)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)

    for idx, frag in enumerate(placeholders):
        escaped = escaped.replace(f"\x00PH{idx}\x00", frag)
    return escaped


def md_to_html(md_text: str) -> str:
    """将常用 Markdown 子集（标题、列表、引用、表格、段落）转为 HTML。"""
    lines = md_text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 标题
        if stripped.startswith(("### ", "## ", "# ")):
            level = len(stripped) - len(stripped.lstrip("#"))
            level = min(max(level, 1), 3)
            content = _format_inline(stripped[level:].strip())
            out.append(f"<h{level}>{content}</h{level}>")
            i += 1
            continue

        # 表格（连续以 | 开头的行）
        if stripped.startswith("|") and "|" in stripped[1:]:
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in rows[1]):
                header = "".join(f"<th>{_format_inline(c)}</th>" for c in rows[0])
                body_rows = []
                for row in rows[2:]:
                    tds = "".join(f"<td>{_format_inline(c)}</td>" for c in row)
                    body_rows.append(f"<tr>{tds}</tr>")
                out.append(
                    f"<table><thead><tr>{header}</tr></thead>"
                    f"<tbody>{''.join(body_rows)}</tbody></table>"
                )
            else:
                for row in rows:
                    out.append(f"<p>{_format_inline(' | '.join(row))}</p>")
            continue

        # 引用块
        if stripped.startswith("> "):
            quote_lines = []
            while i < n and lines[i].strip().startswith("> "):
                quote_lines.append(_format_inline(lines[i].strip()[2:].strip()))
                i += 1
            out.append(f'<blockquote class="note">{"<br>".join(quote_lines)}</blockquote>')
            continue

        # 无序列表
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                item_text = re.sub(r"^[-*]\s+", "", lines[i].strip())
                items.append(f"<li>{_format_inline(item_text)}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
            continue

        # 有序列表
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                items.append(f"<li>{_format_inline(item_text)}</li>")
                i += 1
            out.append(f"<ol>{''.join(items)}</ol>")
            continue

        # 普通段落（合并连续文本行）
        para_lines = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "> ", "|"))
                    or re.match(r"^([-*]|\d+\.)\s+", nxt)):
                break
            para_lines.append(nxt)
            i += 1
        out.append(f"<p>{_format_inline(' '.join(para_lines))}</p>")

    return "\n".join(out)


def parse_article_file(path: Path) -> Article | None:
    """解析单个带 Frontmatter 的 Markdown 文章文件。"""
    raw = path.read_text(encoding="utf-8").lstrip("\ufeff")
    if not raw.startswith("---"):
        return None
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None

    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")

    title = meta.get("title", "").strip()
    if not title:
        return None

    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", meta.get("slug") or path.stem).strip("-")
    if not slug:
        return None

    date = meta.get("date", "").strip()
    category = meta.get("category", "行情观察").strip()
    summary = meta.get("summary", "").strip()
    body_html = md_to_html(parts[2])
    return Article(
        slug=slug,
        title=title,
        date=date,
        category=category,
        summary=summary,
        body_html=body_html,
    )


def load_articles(articles_dir: Path | None = None) -> list[Article]:
    """加载目录下所有合法文章，按发布日期倒序排列。"""
    directory = articles_dir or config.ARTICLES_DIR
    if not directory.exists():
        return []
    items: list[Article] = []
    for path in sorted(directory.glob("*.md")):
        art = parse_article_file(path)
        if art is not None:
            items.append(art)
    items.sort(key=lambda a: (a.date, a.slug), reverse=True)
    return items


def render_articles_index_body(items: list[Article]) -> str:
    """渲染专栏列表页 articles.html 的主体内容。"""
    if not items:
        return (
            "<h1>VPS 行情观察与深度评测专栏</h1>"
            '<p class="muted">暂无文章，每日行情观察与厂商评测持续更新中。</p>'
        )

    cards = []
    for art in items:
        cards.append(
            f'<div class="art-item" style="padding:16px 0;border-bottom:1px solid var(--line);">'
            f'<div class="muted" style="font-size:12px;margin-bottom:4px;">'
            f'<span>{html.escape(art.date)}</span> · '
            f'<span class="tag">{html.escape(art.category)}</span></div>'
            f'<h2 style="margin:4px 0 6px;font-size:17px;">'
            f'<a href="{html.escape(art.href)}">{html.escape(art.title)}</a></h2>'
            f'<p style="margin:0;color:var(--muted);font-size:13.5px;">'
            f'{html.escape(art.summary)}</p></div>'
        )
    return (
        "<h1>VPS 行情观察与深度评测专栏</h1>"
        '<p class="muted">结合每日实时监测数据与实操经验，分享海外 VPS 促销走势、厂商评测与选购避坑指南。</p>'
        f'<div class="art-list">{"".join(cards)}</div>'
    )


def render_latest_articles_section(items: list[Article], limit: int = 4) -> str:
    """渲染首页下方的最新专栏文章卡片区块。"""
    if not items:
        return ""
    cards = []
    for art in items[:limit]:
        cards.append(
            f'<div class="guide-item">'
            f'<div class="muted" style="font-size:11.5px;margin-bottom:4px;">'
            f'{html.escape(art.date)} · {html.escape(art.category)}</div>'
            f'<b><a href="{html.escape(art.href)}">{html.escape(art.title)}</a></b>'
            f'<div>{html.escape(art.summary)}</div>'
            f'</div>'
        )
    return (
        '<section class="card guide" aria-label="最新行情观察与深度评测">\n'
        '  <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:12px;">\n'
        '    <h2 style="margin:0;">最新行情观察与深度评测</h2>\n'
        '    <a href="articles.html" style="font-size:12.5px;">查看全部专栏 &rarr;</a>\n'
        '  </div>\n'
        f'  <div class="guide-grid">{"".join(cards)}</div>\n'
        '</section>'
    )
