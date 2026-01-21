"""Textual widgets for Claude Code UI.

Re-exports all widgets from submodules for backward compatibility.
"""

# Base classes and mixins
from claudechic.widgets.base import (
    ClickableMixin,
    PointerMixin,
    CopyButton,
    CopyableMixin,
    ToolWidget,
)

# Primitives
from claudechic.widgets.primitives import (
    Button,
    QuietCollapsible,
    AutoHideScroll,
    Spinner,
)

# Content widgets
from claudechic.widgets.content import (
    ChatMessage,
    ChatInput,
    ThinkingIndicator,
    ImageAttachments,
    ErrorMessage,
    SystemInfo,
    ChatAttachment,
    ToolUseWidget,
    TaskWidget,
    AgentToolWidget,
    AgentListWidget,
    ShellOutputWidget,
    EditPlanRequested,
    DiffWidget,
    TodoWidget,
    TodoPanel,
)

# Input widgets
from claudechic.widgets.input import TextAreaAutoComplete, HistorySearch

# Layout widgets
from claudechic.widgets.layout import (
    ChatView,
    AgentItem,
    AgentSidebar,
    WorktreeItem,
    PlanItem,
    PlanSection,
    SidebarSection,
    HamburgerButton,
    SessionItem,
    AutoEditLabel,
    ModelLabel,
    StatusFooter,
    IndicatorWidget,
    CPUBar,
    ContextBar,
    ProcessIndicator,
    ProcessPanel,
    ProcessItem,
)

# Base re-exports (ClickableLabel used by layout widgets)
from claudechic.widgets.base import ClickableLabel

# Data classes (re-exported for convenience)
from claudechic.processes import BackgroundProcess

# Report widgets
from claudechic.widgets.reports import UsageReport, ContextReport

# Modal screens
from claudechic.widgets.modals import ProfileModal, ProcessModal

# Prompts
from claudechic.widgets.prompts import (
    BasePrompt,
    SelectionPrompt,
    QuestionPrompt,
    ModelPrompt,
    WorktreePrompt,
    UncommittedChangesPrompt,
)

__all__ = [
    # Base
    "ClickableMixin",
    "PointerMixin",
    "CopyButton",
    "CopyableMixin",
    "ToolWidget",
    # Primitives
    "Button",
    "QuietCollapsible",
    "AutoHideScroll",
    "Spinner",
    # Content
    "ChatMessage",
    "ChatInput",
    "ThinkingIndicator",
    "ImageAttachments",
    "ErrorMessage",
    "SystemInfo",
    "ChatAttachment",
    "ToolUseWidget",
    "TaskWidget",
    "AgentToolWidget",
    "AgentListWidget",
    "ShellOutputWidget",
    "EditPlanRequested",
    "DiffWidget",
    "TodoWidget",
    "TodoPanel",
    # Input
    "TextAreaAutoComplete",
    "HistorySearch",
    # Layout
    "ChatView",
    "AgentItem",
    "AgentSidebar",
    "WorktreeItem",
    "SessionItem",
    "PlanItem",
    "PlanSection",
    "SidebarSection",
    "HamburgerButton",
    "ClickableLabel",
    "AutoEditLabel",
    "ModelLabel",
    "StatusFooter",
    "IndicatorWidget",
    "CPUBar",
    "ContextBar",
    "ProcessIndicator",
    "ProcessPanel",
    "ProcessItem",
    "BackgroundProcess",
    # Reports
    "UsageReport",
    "ContextReport",
    # Modals
    "ProfileModal",
    "ProcessModal",
    # Prompts
    "BasePrompt",
    "SelectionPrompt",
    "QuestionPrompt",
    "ModelPrompt",
    "WorktreePrompt",
    "UncommittedChangesPrompt",
]
