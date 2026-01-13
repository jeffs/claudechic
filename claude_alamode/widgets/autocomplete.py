"""Autocomplete widget for TextArea - supports slash commands and file paths."""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from operator import itemgetter

from textual import on
from textual.app import ComposeResult
from textual.cache import LRUCache
from textual.content import Content
from textual.css.query import NoMatches
from textual.geometry import Offset, Region, Spacing
from textual.style import Style
from textual.widget import Widget
from textual.widgets import OptionList, TextArea
from textual.widgets.option_list import Option
from textual_autocomplete.fuzzy_search import FuzzySearch

from rich.text import Text


@dataclass
class TargetState:
    """State of the target TextArea."""
    text: str
    cursor_position: int  # Linear position in text


class DropdownItem(Option):
    """A single autocomplete option."""

    def __init__(
        self,
        main: str | Content,
        prefix: str | Content | None = None,
        id: str | None = None,
        disabled: bool = False,
    ) -> None:
        self.main = Content(main) if isinstance(main, str) else main
        self.prefix = Content(prefix) if isinstance(prefix, str) else prefix
        prompt = self.main
        if self.prefix:
            prompt = Content.assemble(self.prefix, self.main)
        super().__init__(prompt, id, disabled)

    @property
    def value(self) -> str:
        return self.main.plain


class TextAreaAutoComplete(Widget):
    """Autocomplete dropdown for TextArea widgets.

    Supports two modes:
    - Slash commands: triggered by `/` at start of input
    - File paths: triggered by `@` anywhere in input
    """

    DEFAULT_CSS = """\
    TextAreaAutoComplete {
        height: auto;
        width: auto;
        max-height: 12;
        display: none;
        background: $surface;
        overlay: screen;

        & OptionList {
            width: auto;
            height: auto;
            border: none;
            padding: 0;
            margin: 0;
            scrollbar-size-vertical: 1;
            text-wrap: nowrap;
            color: $foreground;
            background: transparent;
        }

        & .autocomplete--highlight-match {
            text-style: bold;
        }
    }
    """

    COMPONENT_CLASSES = {"autocomplete--highlight-match"}

    def __init__(
        self,
        target: TextArea | str,
        slash_commands: list[str] | None = None,
        base_path: str | Path = ".",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._target = target
        self.slash_commands = slash_commands or []
        self.base_path = Path(base_path)
        self._fuzzy_search = FuzzySearch()
        self._directory_cache: LRUCache[str, list[os.DirEntry]] = LRUCache(100)
        self._mode: str | None = None  # "slash" or "path" or None
        self._trigger_pos: int = 0  # Position of / or @
        self._completing: bool = False  # Flag to prevent re-showing during completion

    def compose(self) -> ComposeResult:
        option_list = OptionList()
        option_list.can_focus = False
        yield option_list

    @property
    def target(self) -> TextArea:
        if isinstance(self._target, TextArea):
            return self._target
        return self.screen.query_one(self._target, TextArea)

    @property
    def option_list(self) -> OptionList:
        return self.query_one(OptionList)

    def on_mount(self) -> None:
        self.target.message_signal.subscribe(self, self._on_target_message)
        # Watch selection for cursor moves
        self.watch(self.target, "selection", self._on_selection_change)
        # Register with target for key interception
        if hasattr(self.target, "_autocomplete"):
            self.target._autocomplete = self

    def _on_selection_change(self) -> None:
        """Called when cursor position changes."""
        self._handle_text_change()

    def _on_target_message(self, event) -> None:
        """Handle messages from the target TextArea."""
        from textual import events

        if isinstance(event, TextArea.Changed):
            self._handle_text_change()
        elif isinstance(event, events.Key):
            self._handle_key(event)

    def _get_cursor_position(self) -> int:
        """Get linear cursor position from TextArea's (row, col) tuple."""
        target = self.target
        row, col = target.cursor_location
        lines = target.text.split("\n")
        pos = sum(len(lines[i]) + 1 for i in range(row)) + col
        return pos

    def _get_target_state(self) -> TargetState:
        return TargetState(
            text=self.target.text,
            cursor_position=self._get_cursor_position(),
        )

    def _handle_text_change(self) -> None:
        """Called when TextArea content changes."""
        if self._completing:
            return
        state = self._get_target_state()
        # Use text up to cursor, or full text if cursor at start (common when setting text directly)
        text_to_check = state.text[:state.cursor_position] if state.cursor_position > 0 else state.text

        # Detect mode
        self._mode = None
        self._trigger_pos = 0

        # Check for slash command at start of input
        if state.text.startswith("/"):
            self._mode = "slash"
            self._trigger_pos = 0
        # Check for @ path reference
        elif "@" in text_to_check:
            at_pos = text_to_check.rfind("@")
            # Make sure it's not in the middle of a word
            if at_pos == 0 or text_to_check[at_pos - 1] in " \n\t":
                self._mode = "path"
                self._trigger_pos = at_pos

        if self._mode:
            self._rebuild_options(state)
            self._align_to_target()
            if self.option_list.option_count > 0:
                search = self._get_search_string(state)
                # Show if there's something to filter, or we're at the trigger
                if self._should_show(search):
                    self.action_show()
                else:
                    self.action_hide()
            else:
                self.action_hide()
        else:
            self.action_hide()

    def _should_show(self, search_string: str) -> bool:
        """Determine if dropdown should be shown."""
        option_count = self.option_list.option_count
        if option_count == 0:
            return False
        if option_count == 1:
            first_option = self.option_list.get_option_at_index(0).prompt
            text = first_option.plain if isinstance(first_option, Text) else str(first_option)
            # For slash commands, compare with the full command
            if self._mode == "slash":
                return text != search_string.lstrip("/")
            return text != search_string
        return True

    def _get_search_string(self, state: TargetState) -> str:
        """Get the string to search/filter with."""
        # Use text up to cursor, or full text if cursor at start
        text_to_check = state.text[:state.cursor_position] if state.cursor_position > 0 else state.text

        if self._mode == "slash":
            return text_to_check  # Include the /
        elif self._mode == "path":
            path_text = text_to_check[self._trigger_pos + 1:]  # After @
            if "/" in path_text:
                return path_text[path_text.rfind("/") + 1:]
            return path_text
        return ""

    def _get_candidates(self, state: TargetState) -> list[DropdownItem]:
        """Get autocomplete candidates based on mode."""
        if self._mode == "slash":
            return [DropdownItem(cmd, prefix="⚡ ") for cmd in self.slash_commands]
        elif self._mode == "path":
            return self._get_path_candidates(state)
        return []

    def _get_path_candidates(self, state: TargetState) -> list[DropdownItem]:
        """Get filesystem path candidates."""
        text_before_cursor = state.text[:state.cursor_position]
        path_text = text_before_cursor[self._trigger_pos + 1:]  # After @

        # Determine directory to list
        if "/" in path_text:
            last_slash = path_text.rfind("/")
            dir_path = path_text[:last_slash] or "/"
            directory = self.base_path / dir_path
        else:
            directory = self.base_path

        cache_key = str(directory)
        entries = self._directory_cache.get(cache_key)
        if entries is None:
            try:
                entries = list(os.scandir(directory))
                self._directory_cache[cache_key] = entries
            except OSError:
                return []

        results = []
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue  # Skip dotfiles
            if entry.is_dir():
                results.append(DropdownItem(name + "/", prefix="📂 "))
            else:
                results.append(DropdownItem(name, prefix="📄 "))

        # Sort: directories first, then alphabetically
        results.sort(key=lambda x: (not x.value.endswith("/"), x.value.lower()))
        return results

    def _rebuild_options(self, state: TargetState) -> None:
        """Rebuild dropdown options."""
        option_list = self.option_list
        option_list.clear_options()

        candidates = self._get_candidates(state)
        search_string = self._get_search_string(state)
        matches = self._get_matches(candidates, search_string)

        if matches:
            option_list.add_options(matches)
            option_list.highlighted = 0

    def _get_matches(
        self, candidates: list[DropdownItem], search_string: str
    ) -> list[DropdownItem]:
        """Filter and score candidates against search string."""
        if not search_string or (self._mode == "slash" and search_string == "/"):
            return candidates

        # For slash commands, strip the leading /
        query = search_string.lstrip("/") if self._mode == "slash" else search_string

        matches_and_scores: list[tuple[DropdownItem, float]] = []
        for candidate in candidates:
            candidate_string = candidate.value.rstrip("/")  # Don't match trailing /
            score, offsets = self._fuzzy_search.match(query, candidate_string)
            if score > 0:
                highlighted = self._apply_highlights(candidate.main, offsets)
                item = DropdownItem(
                    main=highlighted,
                    prefix=candidate.prefix,
                    id=candidate.id,
                    disabled=candidate.disabled,
                )
                matches_and_scores.append((item, score))

        matches_and_scores.sort(key=itemgetter(1), reverse=True)
        return [m for m, _ in matches_and_scores]

    def _apply_highlights(self, candidate: Content, offsets: tuple[int, ...]) -> Content:
        """Highlight matched characters."""
        match_style = Style.from_rich_style(
            self.get_component_rich_style("autocomplete--highlight-match", partial=True)
        )
        plain = candidate.plain
        for offset in offsets:
            if offset < len(plain) and not plain[offset].isspace():
                candidate = candidate.stylize(match_style, offset, offset + 1)
        return candidate

    def _align_to_target(self) -> None:
        """Position dropdown near cursor."""
        try:
            x, y = self.target.cursor_screen_offset
            dropdown = self.option_list
            width, height = dropdown.outer_size
            x, y, _, _ = Region(x - 1, y + 1, width, height).constrain(
                "inside", "none", Spacing.all(0), self.screen.scrollable_content_region
            )
            self.absolute_offset = Offset(x, y)
        except Exception:
            pass

    def handle_key(self, key: str) -> bool:
        """Handle a key press. Returns True if the key was consumed.

        Call this from the target widget's key handler to intercept keys
        before the target processes them.
        """
        try:
            option_list = self.option_list
        except NoMatches:
            return False

        if not option_list.option_count or not self.display:
            return False

        highlighted = option_list.highlighted or 0

        if key == "down":
            option_list.highlighted = (highlighted + 1) % option_list.option_count
            return True
        elif key == "up":
            option_list.highlighted = (highlighted - 1) % option_list.option_count
            return True
        elif key == "tab":
            self._complete(highlighted)
            return True
        elif key == "enter":
            self._complete(highlighted)
            return True
        elif key == "escape":
            self.action_hide()
            return True

        return False

    def _handle_key(self, event) -> None:
        """Handle key events from target (via message_signal, for navigation only)."""
        from textual import events

        if not isinstance(event, events.Key):
            return

        # Only handle navigation keys via signal (tab/enter handled via ChatInput override)
        if event.key in ("down", "up", "escape"):
            self.handle_key(event.key)

    def _complete(self, option_index: int) -> None:
        """Apply the selected completion."""
        if not self.display or self.option_list.option_count == 0:
            return

        option = self.option_list.get_option_at_index(option_index)
        value = option.prompt.plain if isinstance(option.prompt, Text) else str(option.prompt)
        # Strip prefix (emoji + space)
        if value.startswith(("⚡ ", "📂 ", "📄 ")):
            value = value[2:]

        state = self._get_target_state()
        keep_open = self._mode == "path" and value.endswith("/")

        self._completing = True
        self._apply_completion(value, state)

        # Keep open for directory navigation, otherwise hide after refresh cycle
        if keep_open:
            self._completing = False
            self._handle_text_change()
        else:
            # Use call_after_refresh to hide after all pending updates
            def hide_and_reset():
                self._completing = False
                self.action_hide()
            self.call_after_refresh(hide_and_reset)

    def _apply_completion(self, value: str, state: TargetState) -> None:
        """Insert the completion into the TextArea."""
        target = self.target
        text = state.text
        # Use actual text length if cursor is at 0
        cursor_pos = state.cursor_position if state.cursor_position > 0 else len(text)

        if self._mode == "slash":
            # Replace entire text when completing a slash command
            # (slash commands are always at the start and standalone)
            target.text = value
            target.move_cursor((0, len(value)))
        elif self._mode == "path":
            path_text = text[self._trigger_pos + 1:cursor_pos]
            if "/" in path_text:
                # Replace only the filename part
                last_slash = path_text.rfind("/")
                replace_start = self._trigger_pos + 1 + last_slash + 1
            else:
                replace_start = self._trigger_pos + 1

            new_text = text[:replace_start] + value + text[cursor_pos:]
            target.text = new_text
            new_cursor = replace_start + len(value)
            # Convert linear position to (row, col)
            lines = new_text[:new_cursor].split("\n")
            row = len(lines) - 1
            col = len(lines[-1])
            target.move_cursor((row, col))

    def action_hide(self) -> None:
        self.styles.display = "none"

    def action_show(self) -> None:
        self.styles.display = "block"

    @on(OptionList.OptionSelected)
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle mouse click on option."""
        self._complete(event.option_index)
