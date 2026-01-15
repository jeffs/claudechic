"""Theme definition for Claude à la Mode."""

from textual.theme import Theme

# Custom theme for Claude à la Mode
ALAMODE_THEME = Theme(
    name="alamode",
    primary="#cc7700",
    secondary="#5599dd",  # Sky blue for syntax highlighting
    accent="#445566",
    background="black",
    surface="#111111",
    panel="#333333",  # Used for borders and subtle UI elements
    success="#5599dd",  # Same as secondary - strings in code
    warning="#ffaa33",  # Bright orange - numbers in code
    error="#ff6666",  # Red - errors
    dark=True,
)

# Export individual colors for use in Python code (e.g., Rich Text styling)
PRIMARY = ALAMODE_THEME.primary
