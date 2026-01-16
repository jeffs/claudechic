"""Textual widgets for Claude Code UI."""

from claude_alamode.widgets.indicators import CPUBar, ContextBar
from claude_alamode.widgets.chat import ChatMessage, ChatInput, ThinkingIndicator, ImageAttachments, ErrorMessage, ChatAttachment
from claude_alamode.widgets.tools import ToolUseWidget, TaskWidget, AgentToolWidget
from claude_alamode.widgets.diff import DiffWidget
from claude_alamode.widgets.todo import TodoWidget, TodoPanel
from claude_alamode.widgets.prompts import BasePrompt, SelectionPrompt, QuestionPrompt, SessionItem
from claude_alamode.widgets.autocomplete import TextAreaAutoComplete
from claude_alamode.widgets.agents import AgentItem, AgentSidebar, WorktreeItem
from claude_alamode.widgets.scroll import AutoHideScroll
from claude_alamode.widgets.chat_view import ChatView
from claude_alamode.widgets.history_search import HistorySearch

__all__ = [
    "CPUBar",
    "ContextBar",
    "ChatMessage",
    "ChatInput",
    "ChatAttachment",
    "ThinkingIndicator",
    "ImageAttachments",
    "ErrorMessage",
    "ToolUseWidget",
    "TaskWidget",
    "AgentToolWidget",
    "DiffWidget",
    "TodoWidget",
    "TodoPanel",
    "BasePrompt",
    "SelectionPrompt",
    "QuestionPrompt",
    "SessionItem",
    "TextAreaAutoComplete",
    "AgentItem",
    "AgentSidebar",
    "WorktreeItem",
    "AutoHideScroll",
    "ChatView",
    "HistorySearch",
]
