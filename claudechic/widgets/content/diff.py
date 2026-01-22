"""Syntax-highlighted diff widget."""

import difflib
import re
from functools import lru_cache

from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from textual.content import Content, Span
from textual.containers import HorizontalScroll
from textual.highlight import HighlightTheme
from textual.widgets import Static

from claudechic.formatting import get_lang_from_path


# Colors - line backgrounds
REMOVED_BG = "#301010"
ADDED_BG = "#103010"
# Word-level change highlights
REMOVED_WORD_STYLE = "underline on #501818"
ADDED_WORD_STYLE = "underline on #185018"


@lru_cache(maxsize=64)
def _get_cached_lexer(language: str):
    """Cache Pygments lexers to avoid repeated loading (~15% CPU savings)."""
    try:
        return get_lexer_by_name(language, stripnl=False, ensurenl=True, tabsize=8)
    except ClassNotFound:
        return None


def _highlight_text(text: str, language: str) -> Content:
    """Syntax highlight text using cached lexer and default HighlightTheme."""
    if not language:
        return Content(text)

    lexer = _get_cached_lexer(language)
    if lexer is None:
        return Content(text)

    text = "\n".join(text.splitlines())
    token_start = 0
    spans: list[Span] = []

    for token_type, token in lexer.get_tokens(text):
        token_end = token_start + len(token)
        current_type = token_type
        while True:
            if style := HighlightTheme.STYLES.get(current_type):
                spans.append(Span(token_start, token_end, style))
                break
            if (current_type := current_type.parent) is None:
                break
        token_start = token_end

    return Content(text, spans=spans).stylize_before("$text")


def _highlight_lines(text: str, language: str) -> list[Content]:
    """Syntax highlight text and split into lines."""
    if not text:
        return []
    return _highlight_text(text, language).split("\n")


def _word_diff_spans(
    old_line: str, new_line: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Compute character spans that changed between two lines.

    Returns (old_spans, new_spans) where each span is (start, end) of changed text.
    """

    def tokenize(s: str) -> list[tuple[str, int, int]]:
        """Return list of (token, start, end). Splits on whitespace and punctuation."""
        result = []
        # Match: words, individual punctuation, or whitespace runs
        for m in re.finditer(r"\w+|[^\w\s]|\s+", s):
            result.append((m.group(), m.start(), m.end()))
        return result

    old_tokens = tokenize(old_line)
    new_tokens = tokenize(new_line)
    old_strs = [t[0] for t in old_tokens]
    new_strs = [t[0] for t in new_tokens]

    sm = difflib.SequenceMatcher(None, old_strs, new_strs)
    old_spans = []
    new_spans = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace") and old_tokens:
            start = old_tokens[i1][1] if i1 < len(old_tokens) else len(old_line)
            end = (
                old_tokens[i2 - 1][2]
                if i2 > 0 and i2 - 1 < len(old_tokens)
                else len(old_line)
            )
            if start < end:
                old_spans.append((start, end))
        if tag in ("insert", "replace") and new_tokens:
            start = new_tokens[j1][1] if j1 < len(new_tokens) else len(new_line)
            end = (
                new_tokens[j2 - 1][2]
                if j2 > 0 and j2 - 1 < len(new_tokens)
                else len(new_line)
            )
            if start < end:
                new_spans.append((start, end))

    return old_spans, new_spans


def _build_line_content(
    line_content: Content,
    bg_style: str,
    highlight_spans: list[tuple[int, int]] | None = None,
    highlight_style: str = "",
) -> Content:
    """Apply background style to line, with optional subtle highlights for changed spans.

    Preserves syntax highlighting from the input content.
    """
    # Apply base background to entire line (preserves syntax highlighting)
    result = line_content.stylize(bg_style, 0, len(line_content))

    # Apply subtle underline for changed regions (doesn't obscure syntax colors)
    if highlight_spans and highlight_style:
        for start, end in highlight_spans:
            # Clamp to line length
            start = min(start, len(result))
            end = min(end, len(result))
            if start < end:
                result = result.stylize(highlight_style, start, end)

    return result


class DiffContent(Static):
    """Inner static widget that renders the diff content without wrapping."""

    DEFAULT_CSS = """
    DiffContent {
        color: $text;
        width: auto;
    }
    """


class DiffWidget(HorizontalScroll):
    """Displays a syntax-highlighted diff between two code strings."""

    DEFAULT_CSS = """
    DiffWidget {
        height: auto;
        max-height: 100%;
        scrollbar-size: 1 1;
    }
    """

    # Minimum width to show side-by-side (each side needs ~60 chars)
    SIDE_BY_SIDE_MIN_WIDTH = 120

    def __init__(
        self,
        old: str,
        new: str,
        path: str = "",
        context_lines: int = 3,
        replace_all: bool = False,
        old_start: int = 1,
        new_start: int = 1,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._old = old
        self._new = new
        self._path = path
        self._context_lines = context_lines
        self._replace_all = replace_all
        self._old_start = old_start
        self._new_start = new_start

    def compose(self):
        content = self._render_diff()
        yield DiffContent(content)

    def on_resize(self) -> None:
        """Re-render diff when width changes (switch between unified/side-by-side)."""
        try:
            content_widget = self.query_one(DiffContent)
            content_widget.update(self._render_diff())
        except Exception:
            pass

    def _use_side_by_side(self) -> bool:
        """Determine if we should use side-by-side view based on available width."""
        try:
            return self.app.size.width >= self.SIDE_BY_SIDE_MIN_WIDTH
        except Exception:
            return False

    def _render_diff(self) -> Content:
        """Build the complete diff display."""
        # For replace_all edits, show simple pattern replacement
        if self._replace_all:
            return Content.assemble(
                Content.styled("- ", f"red on {REMOVED_BG}"),
                Content.styled(self._old, f"underline on {REMOVED_BG}"),
                Content.styled("\n", ""),
                Content.styled("+ ", f"green on {ADDED_BG}"),
                Content.styled(self._new, f"underline on {ADDED_BG}"),
                Content.styled("\n(all occurrences)", "dim"),
            )

        if self._use_side_by_side():
            return self._render_side_by_side()
        return self._render_unified()

    def _prepare_diff(self):
        """Common setup for both unified and side-by-side rendering.

        Returns (old_lines, new_lines, old_highlighted, new_highlighted, grouped, gutter_width)
        or None if no changes.
        """
        old_lines = self._old.splitlines() if self._old else []
        new_lines = self._new.splitlines() if self._new else []

        if old_lines == new_lines:
            return None

        lang = get_lang_from_path(self._path)
        old_highlighted = _highlight_lines(self._old, lang)
        new_highlighted = _highlight_lines(self._new, lang)

        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        grouped = list(sm.get_grouped_opcodes(self._context_lines))

        max_old = self._old_start + len(old_lines) - 1 if old_lines else self._old_start
        max_new = self._new_start + len(new_lines) - 1 if new_lines else self._new_start
        gutter_width = max(len(str(max_old)), len(str(max_new)))

        return (
            old_lines,
            new_lines,
            old_highlighted,
            new_highlighted,
            grouped,
            gutter_width,
        )

    def _render_unified(self) -> Content:
        """Render unified diff (stacked - / + lines)."""
        prep = self._prepare_diff()
        if prep is None:
            return Content.styled("No changes", "dim")
        (
            old_lines,
            new_lines,
            old_highlighted,
            new_highlighted,
            grouped,
            gutter_width,
        ) = prep

        def make_gutter(old_num: int | None, new_num: int | None) -> Content:
            old_str = (
                str(old_num).rjust(gutter_width) if old_num else " " * gutter_width
            )
            new_str = (
                str(new_num).rjust(gutter_width) if new_num else " " * gutter_width
            )
            return Content.styled(f"{old_str} {new_str} ", "#666666")

        parts: list[Content] = []

        for group_idx, group in enumerate(grouped):
            if group_idx > 0:
                sep_width = gutter_width * 2 + 2
                sep = Content.styled(" " * sep_width + " ···\n", "dim")
                parts.append(sep)

            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for di, i in enumerate(range(i1, i2)):
                        j = j1 + di
                        gutter = make_gutter(self._old_start + i, self._new_start + j)
                        code = (
                            old_highlighted[i]
                            if i < len(old_highlighted)
                            else Content("")
                        )
                        line = Content.assemble(
                            gutter,
                            Content("  "),
                            code.stylize("dim", 0, len(code)),
                            Content("\n"),
                        )
                        parts.append(line)

                elif tag == "delete":
                    for i in range(i1, i2):
                        gutter = make_gutter(self._old_start + i, None)
                        code = (
                            old_highlighted[i]
                            if i < len(old_highlighted)
                            else Content("")
                        )
                        styled_code = _build_line_content(code, f"on {REMOVED_BG}")
                        indicator = Content.styled("- ", f"red on {REMOVED_BG}")
                        line = Content.assemble(
                            gutter, indicator, styled_code, Content("\n")
                        )
                        parts.append(line)

                elif tag == "insert":
                    for j in range(j1, j2):
                        gutter = make_gutter(None, self._new_start + j)
                        code = (
                            new_highlighted[j]
                            if j < len(new_highlighted)
                            else Content("")
                        )
                        styled_code = _build_line_content(code, f"on {ADDED_BG}")
                        indicator = Content.styled("+ ", f"green on {ADDED_BG}")
                        line = Content.assemble(
                            gutter, indicator, styled_code, Content("\n")
                        )
                        parts.append(line)

                elif tag == "replace":
                    for idx, i in enumerate(range(i1, i2)):
                        old_line_text = old_lines[i] if i < len(old_lines) else ""
                        j = j1 + idx
                        if j < j2:
                            new_line_text = new_lines[j] if j < len(new_lines) else ""
                            old_spans, _ = _word_diff_spans(
                                old_line_text, new_line_text
                            )
                        else:
                            old_spans = []

                        gutter = make_gutter(self._old_start + i, None)
                        code = (
                            old_highlighted[i]
                            if i < len(old_highlighted)
                            else Content("")
                        )
                        styled_code = _build_line_content(
                            code, f"on {REMOVED_BG}", old_spans, REMOVED_WORD_STYLE
                        )
                        indicator = Content.styled("- ", f"red on {REMOVED_BG}")
                        line = Content.assemble(
                            gutter, indicator, styled_code, Content("\n")
                        )
                        parts.append(line)

                    for idx, j in enumerate(range(j1, j2)):
                        new_line_text = new_lines[j] if j < len(new_lines) else ""
                        i = i1 + idx
                        if i < i2:
                            old_line_text = old_lines[i] if i < len(old_lines) else ""
                            _, new_spans = _word_diff_spans(
                                old_line_text, new_line_text
                            )
                        else:
                            new_spans = []

                        gutter = make_gutter(None, self._new_start + j)
                        code = (
                            new_highlighted[j]
                            if j < len(new_highlighted)
                            else Content("")
                        )
                        styled_code = _build_line_content(
                            code, f"on {ADDED_BG}", new_spans, ADDED_WORD_STYLE
                        )
                        indicator = Content.styled("+ ", f"green on {ADDED_BG}")
                        line = Content.assemble(
                            gutter, indicator, styled_code, Content("\n")
                        )
                        parts.append(line)

        if not parts:
            return Content.styled("No changes", "dim")

        return Content.assemble(*parts).rstrip("\n")

    def _render_side_by_side(self) -> Content:
        """Render side-by-side diff with old on left, new on right."""
        prep = self._prepare_diff()
        if prep is None:
            return Content.styled("No changes", "dim")
        (
            old_lines,
            new_lines,
            old_highlighted,
            new_highlighted,
            grouped,
            gutter_width,
        ) = prep
        gutter_width = max(gutter_width, 3)  # Minimum width for side-by-side

        # Calculate column width - split available width between sides
        try:
            total_width = self.app.size.width - 35  # Account for sidebar
        except Exception:
            total_width = 120
        # Each side: gutter + space + code
        # Layout: [gutter code] │ [gutter code]
        col_width = max((total_width - 3) // 2, 40)  # -3 for separator
        code_width = col_width - gutter_width - 1

        def pad_or_truncate(content: Content, width: int) -> Content:
            """Pad content to width or truncate if too long."""
            text_len = len(content)
            if text_len >= width:
                # Truncate - just take first `width` chars (preserving styles)
                return content[:width]
            # Pad with spaces
            padding = " " * (width - text_len)
            return Content.assemble(content, Content(padding))

        def make_left_col(line_num: int | None, code: Content, bg: str = "") -> Content:
            """Build left column: gutter + code, padded."""
            gutter = (
                Content.styled(str(line_num).rjust(gutter_width) + " ", "#666666")
                if line_num
                else Content.styled(" " * (gutter_width + 1), "#666666")
            )
            if bg:
                code = _build_line_content(code, bg)
            padded = pad_or_truncate(code, code_width)
            return Content.assemble(gutter, padded)

        def make_right_col(
            line_num: int | None, code: Content, bg: str = ""
        ) -> Content:
            """Build right column: gutter + code."""
            gutter = (
                Content.styled(str(line_num).rjust(gutter_width) + " ", "#666666")
                if line_num
                else Content.styled(" " * (gutter_width + 1), "#666666")
            )
            if bg:
                code = _build_line_content(code, bg)
            return Content.assemble(gutter, code)

        separator = Content.styled(" │ ", "dim")
        parts: list[Content] = []

        for group_idx, group in enumerate(grouped):
            if group_idx > 0:
                sep_line = Content.styled(
                    " " * col_width + " ··· " + " " * col_width + "\n", "dim"
                )
                parts.append(sep_line)

            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    # Both sides show the same content
                    for di, i in enumerate(range(i1, i2)):
                        j = j1 + di
                        old_code = (
                            old_highlighted[i]
                            if i < len(old_highlighted)
                            else Content("")
                        )
                        new_code = (
                            new_highlighted[j]
                            if j < len(new_highlighted)
                            else Content("")
                        )
                        left = make_left_col(self._old_start + i, old_code)
                        right = make_right_col(self._new_start + j, new_code)
                        line = Content.assemble(
                            left.stylize("dim", 0, len(left)),
                            separator,
                            right.stylize("dim", 0, len(right)),
                            Content("\n"),
                        )
                        parts.append(line)

                elif tag == "delete":
                    # Left side has content, right side empty
                    for i in range(i1, i2):
                        old_code = (
                            old_highlighted[i]
                            if i < len(old_highlighted)
                            else Content("")
                        )
                        left = make_left_col(
                            self._old_start + i, old_code, f"on {REMOVED_BG}"
                        )
                        right = make_right_col(None, Content(""))
                        line = Content.assemble(left, separator, right, Content("\n"))
                        parts.append(line)

                elif tag == "insert":
                    # Left side empty, right side has content
                    for j in range(j1, j2):
                        new_code = (
                            new_highlighted[j]
                            if j < len(new_highlighted)
                            else Content("")
                        )
                        left = make_left_col(None, Content(""))
                        left = pad_or_truncate(left, col_width)
                        right = make_right_col(
                            self._new_start + j, new_code, f"on {ADDED_BG}"
                        )
                        line = Content.assemble(left, separator, right, Content("\n"))
                        parts.append(line)

                elif tag == "replace":
                    # Pair up old/new lines side by side
                    old_count = i2 - i1
                    new_count = j2 - j1
                    max_count = max(old_count, new_count)

                    for idx in range(max_count):
                        # Left side (old)
                        if idx < old_count:
                            i = i1 + idx
                            old_code = (
                                old_highlighted[i]
                                if i < len(old_highlighted)
                                else Content("")
                            )
                            old_text = old_lines[i] if i < len(old_lines) else ""
                            # Get word diff spans if there's a matching new line
                            if idx < new_count:
                                j = j1 + idx
                                new_text = new_lines[j] if j < len(new_lines) else ""
                                old_spans, _ = _word_diff_spans(old_text, new_text)
                            else:
                                old_spans = []
                            styled_old = _build_line_content(
                                old_code,
                                f"on {REMOVED_BG}",
                                old_spans,
                                REMOVED_WORD_STYLE,
                            )
                            left = make_left_col(self._old_start + i, styled_old)
                        else:
                            left = make_left_col(None, Content(""))

                        left = pad_or_truncate(left, col_width)

                        # Right side (new)
                        if idx < new_count:
                            j = j1 + idx
                            new_code = (
                                new_highlighted[j]
                                if j < len(new_highlighted)
                                else Content("")
                            )
                            new_text = new_lines[j] if j < len(new_lines) else ""
                            # Get word diff spans if there's a matching old line
                            if idx < old_count:
                                i = i1 + idx
                                old_text = old_lines[i] if i < len(old_lines) else ""
                                _, new_spans = _word_diff_spans(old_text, new_text)
                            else:
                                new_spans = []
                            styled_new = _build_line_content(
                                new_code,
                                f"on {ADDED_BG}",
                                new_spans,
                                ADDED_WORD_STYLE,
                            )
                            right = make_right_col(self._new_start + j, styled_new)
                        else:
                            right = make_right_col(None, Content(""))

                        line = Content.assemble(left, separator, right, Content("\n"))
                        parts.append(line)

        if not parts:
            return Content.styled("No changes", "dim")

        return Content.assemble(*parts).rstrip("\n")
