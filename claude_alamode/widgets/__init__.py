"""Textual widgets for Claude Code UI."""

from claude_alamode.widgets.header import CPUBar, ContextBar, HeaderIndicators, ContextHeader
from claude_alamode.widgets.chat import ChatMessage, ChatInput, ThinkingIndicator, ImageAttachments, ErrorMessage
from claude_alamode.widgets.tools import ToolUseWidget, TaskWidget
from claude_alamode.widgets.todo import TodoWidget, TodoPanel
from claude_alamode.widgets.prompts import BasePrompt, SelectionPrompt, QuestionPrompt, SessionItem
from claude_alamode.widgets.autocomplete import TextAreaAutoComplete
from claude_alamode.widgets.agents import AgentItem, AgentSidebar, WorktreeItem
from claude_alamode.widgets.scroll import AutoHideScroll

__all__ = [
    "CPUBar",
    "ContextBar",
    "HeaderIndicators",
    "ContextHeader",
    "ChatMessage",
    "ChatInput",
    "ThinkingIndicator",
    "ImageAttachments",
    "ErrorMessage",
    "ToolUseWidget",
    "TaskWidget",
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
]
