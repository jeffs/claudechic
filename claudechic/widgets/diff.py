"""Syntax-highlighted diff widget."""

import difflib
import re

from pygments.token import Token
from textual.content import Content
from textual.containers import HorizontalScroll
from textual.highlight import HighlightTheme, highlight
from textual.widgets import Static

from claudechic.formatting import get_lang_from_path


# Colors - line backgrounds (subtle tint)
REMOVED_BG = "#200000"
ADDED_BG = "#002000"
# Word-level change highlights - subtle background + underline
REMOVED_WORD_STYLE = "underline on #330808"
ADDED_WORD_STYLE = "underline on #083308"


class DiffHighlightTheme(HighlightTheme):
    """Syntax highlighting theme for diffs, aligned with chic theme.

    Uses orange as primary accent, saturated blues for structure,
    and avoids red/green that clash with diff backgrounds.
    """
    STYLES = {
        Token.Comment: "#888888",  # Brighter gray for visibility
        Token.Error: "#ff6b6b",  # Soft red for errors
        Token.Generic.Strong: "bold",
        Token.Generic.Emph: "italic",
        Token.Generic.Error: "#ff6b6b",
        Token.Generic.Heading: "#ff9922 underline",  # Bright orange
        Token.Generic.Subheading: "#ff9922",
        Token.Keyword: "#ff9922",  # Bright orange for keywords
        Token.Keyword.Constant: "#66bbff bold",  # Vivid blue
        Token.Keyword.Namespace: "#ff9922",  # Bright orange
        Token.Keyword.Type: "#66bbff bold",  # Vivid blue
        Token.Literal.Number: "#ffcc66",  # Bright gold
        Token.Literal.String.Backtick: "#888888",  # Gray
        Token.Literal.String: "#77ccff",  # Bright cyan-blue
        Token.Literal.String.Doc: "#77ccff italic",
        Token.Literal.String.Double: "#77ccff",
        Token.Name: "#dddddd",  # Bright base text
        Token.Name.Attribute: "#ffcc66",  # Bright gold
        Token.Name.Builtin: "#66bbff",  # Vivid blue
        Token.Name.Builtin.Pseudo: "#66bbff italic",
        Token.Name.Class: "#ff9922 bold",  # Bright orange
        Token.Name.Constant: "#66bbff",  # Vivid blue
        Token.Name.Decorator: "#ff9922 bold",  # Bright orange
        Token.Name.Function: "#ffcc66",  # Bright gold
        Token.Name.Function.Magic: "#ffcc66",
        Token.Name.Tag: "#ff9922 bold",  # Bright orange
        Token.Name.Variable: "#88ddff",  # Light cyan
        Token.Operator: "#dddddd bold",  # Bright base
        Token.Operator.Word: "#ff9922 bold",  # Bright orange
        Token.Whitespace: "",
    }


def _highlight_lines(text: str, language: str) -> list[Content]:
    """Syntax highlight text and split into lines."""
    if not text:
        return []
    if language:
        highlighted = highlight(text, language=language, theme=DiffHighlightTheme)
    else:
        highlighted = Content(text)
    return highlighted.split("\n")


def _word_diff_spans(old_line: str, new_line: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
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
            end = old_tokens[i2 - 1][2] if i2 > 0 and i2 - 1 < len(old_tokens) else len(old_line)
            if start < end:
                old_spans.append((start, end))
        if tag in ("insert", "replace") and new_tokens:
            start = new_tokens[j1][1] if j1 < len(new_tokens) else len(new_line)
            end = new_tokens[j2 - 1][2] if j2 > 0 and j2 - 1 < len(new_tokens) else len(new_line)
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

    def __init__(
        self,
        old: str,
        new: str,
        path: str = "",
        context_lines: int = 3,
        replace_all: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._old = old
        self._new = new
        self._path = path
        self._context_lines = context_lines
        self._replace_all = replace_all

    def compose(self):
        content = self._render_diff()
        yield DiffContent(content)

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

        old_lines = self._old.splitlines() if self._old else []
        new_lines = self._new.splitlines() if self._new else []

        # Edge case: no changes
        if old_lines == new_lines:
            return Content.styled("No changes", "dim")

        # Get syntax-highlighted versions
        lang = get_lang_from_path(self._path)
        old_highlighted = _highlight_lines(self._old, lang)
        new_highlighted = _highlight_lines(self._new, lang)

        # Compute diff
        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        grouped = list(sm.get_grouped_opcodes(self._context_lines))

        # Calculate gutter width based on max line number (for each column)
        max_old = len(old_lines) or 1
        max_new = len(new_lines) or 1
        gutter_width = max(len(str(max_old)), len(str(max_new)))

        def make_gutter(old_num: int | None, new_num: int | None) -> Content:
            """Create two-column gutter: old_line new_line"""
            old_str = str(old_num).rjust(gutter_width) if old_num else " " * gutter_width
            new_str = str(new_num).rjust(gutter_width) if new_num else " " * gutter_width
            return Content.styled(f"{old_str} {new_str} ", "#666666")

        parts: list[Content] = []

        for group_idx, group in enumerate(grouped):
            # Add separator between groups (hunks)
            if group_idx > 0:
                sep_width = gutter_width * 2 + 2
                sep = Content.styled(" " * sep_width + " ···\n", "dim")
                parts.append(sep)

            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    # Context lines - both line numbers shown
                    for di, i in enumerate(range(i1, i2)):
                        j = j1 + di
                        gutter = make_gutter(i + 1, j + 1)
                        code = old_highlighted[i] if i < len(old_highlighted) else Content("")
                        line = Content.assemble(gutter, Content("  "), code.stylize("dim", 0, len(code)), Content("\n"))
                        parts.append(line)

                elif tag == "delete":
                    # Deleted lines - old line number only
                    for i in range(i1, i2):
                        gutter = make_gutter(i + 1, None)
                        code = old_highlighted[i] if i < len(old_highlighted) else Content("")
                        styled_code = _build_line_content(code, f"on {REMOVED_BG}")
                        indicator = Content.styled("- ", f"red on {REMOVED_BG}")
                        line = Content.assemble(gutter, indicator, styled_code, Content("\n"))
                        parts.append(line)

                elif tag == "insert":
                    # Inserted lines - new line number only
                    for j in range(j1, j2):
                        gutter = make_gutter(None, j + 1)
                        code = new_highlighted[j] if j < len(new_highlighted) else Content("")
                        styled_code = _build_line_content(code, f"on {ADDED_BG}")
                        indicator = Content.styled("+ ", f"green on {ADDED_BG}")
                        line = Content.assemble(gutter, indicator, styled_code, Content("\n"))
                        parts.append(line)

                elif tag == "replace":
                    # Replaced lines - show old then new, with word-level highlights
                    # First show all deleted lines (old line numbers)
                    for idx, i in enumerate(range(i1, i2)):
                        old_line_text = old_lines[i] if i < len(old_lines) else ""
                        # Find matching new line for word diff (if exists)
                        j = j1 + idx
                        if j < j2:
                            new_line_text = new_lines[j] if j < len(new_lines) else ""
                            old_spans, _ = _word_diff_spans(old_line_text, new_line_text)
                        else:
                            old_spans = []

                        gutter = make_gutter(i + 1, None)
                        code = old_highlighted[i] if i < len(old_highlighted) else Content("")
                        styled_code = _build_line_content(
                            code, f"on {REMOVED_BG}",
                            old_spans, REMOVED_WORD_STYLE
                        )
                        indicator = Content.styled("- ", f"red on {REMOVED_BG}")
                        line = Content.assemble(gutter, indicator, styled_code, Content("\n"))
                        parts.append(line)

                    # Then show all inserted lines (new line numbers)
                    for idx, j in enumerate(range(j1, j2)):
                        new_line_text = new_lines[j] if j < len(new_lines) else ""
                        # Find matching old line for word diff (if exists)
                        i = i1 + idx
                        if i < i2:
                            old_line_text = old_lines[i] if i < len(old_lines) else ""
                            _, new_spans = _word_diff_spans(old_line_text, new_line_text)
                        else:
                            new_spans = []

                        gutter = make_gutter(None, j + 1)
                        code = new_highlighted[j] if j < len(new_highlighted) else Content("")
                        styled_code = _build_line_content(
                            code, f"on {ADDED_BG}",
                            new_spans, ADDED_WORD_STYLE
                        )
                        indicator = Content.styled("+ ", f"green on {ADDED_BG}")
                        line = Content.assemble(gutter, indicator, styled_code, Content("\n"))
                        parts.append(line)

        if not parts:
            return Content.styled("No changes", "dim")

        return Content.assemble(*parts).rstrip("\n")
