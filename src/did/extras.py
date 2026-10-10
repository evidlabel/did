"""Optional install extras.

The GUI is a local page served by the stdlib. ``gui`` stays as an empty extra
so existing ``did[gui]`` installs keep resolving. Language models are extra
``models``.
"""


def has_gui() -> bool:
    return True


def require_gui() -> None:
    return None
