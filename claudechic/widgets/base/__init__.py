"""Base classes and mixins for widgets."""

from claudechic.widgets.base.cursor import (
    ClickableMixin,
    PointerMixin,
    set_pointer,
)
from claudechic.widgets.base.copyable import CopyButton, CopyableMixin
from claudechic.widgets.base.clickable import ClickableLabel
from claudechic.widgets.base.tool_protocol import ToolWidget

__all__ = [
    "ClickableMixin",
    "PointerMixin",
    "set_pointer",
    "CopyButton",
    "CopyableMixin",
    "ClickableLabel",
    "ToolWidget",
]
