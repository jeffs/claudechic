"""Textual widgets for Claude Code UI."""

from claude_alamode.widgets.header import CPUBar, ContextBar, HeaderIndicators, ContextHeader
from claude_alamode.widgets.chat import ChatMessage, ChatInput, ThinkingIndicator
from claude_alamode.widgets.tools import ToolUseWidget, TaskWidget
from claude_alamode.widgets.todo import TodoWidget, TodoPanel
from claude_alamode.widgets.prompts import BasePrompt, SelectionPrompt, QuestionPrompt, SessionItem, WorktreePrompt
from claude_alamode.widgets.autocomplete import TextAreaAutoComplete

__all__ = [
    "CPUBar",
    "ContextBar",
    "HeaderIndicators",
    "ContextHeader",
    "ChatMessage",
    "ChatInput",
    "ThinkingIndicator",
    "ToolUseWidget",
    "TaskWidget",
    "TodoWidget",
    "TodoPanel",
    "BasePrompt",
    "SelectionPrompt",
    "QuestionPrompt",
    "SessionItem",
    "WorktreePrompt",
    "TextAreaAutoComplete",
]
