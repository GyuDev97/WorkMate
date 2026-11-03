from __future__ import annotations

import html
import re
from html.parser import HTMLParser

ALLOWED = {"p", "br", "div", "span", "b", "i", "u", "strong", "em", "ul", "ol", "li", "blockquote", "pre", "code", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
           "table", "thead", "tbody", "tr", "td", "th", "a"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "svg", "math", "link", "meta", "base", "title", "head", "noscript", "template"}
SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.I)


class _Sanitizer(HTMLParser):
    """Allow-list HTML sanitizer: no scripts, styles, event handlers, forms or remote images (those can track the reader)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self.blocked_images = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "img":
            self.blocked_images += 1
            self.out.append("<span>[이미지 차단됨]</span>")
            return
        if tag in ALLOWED:
            if tag == "a":
                href = next((value for key, value in attrs if key == "href" and value), "")
                if SAFE_HREF.match(href.strip()):
                    self.out.append(f'<a href="{html.escape(href.strip(), quote=True)}" target="_blank" rel="noopener noreferrer nofollow">')
                else:
                    self.out.append("<a>")
            else:
                self.out.append(f"<{tag}>")
        if tag in {"br", "p", "div", "li", "tr"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ALLOWED and tag not in {"br", "hr"}:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.out.append(html.escape(data))
        self.text.append(data)


def sanitize_html(raw: str) -> tuple[str, str]:
    """Returns (safe_html, plain_text)."""
    parser = _Sanitizer()
    parser.feed(raw or "")
    parser.close()
    text = re.sub(r"\n{3,}", "\n\n", "".join(parser.text)).strip()
    return "".join(parser.out), text
