"""Desktop GUI command."""

import sys

from treeparse import command

from did import extras


def gui():
    """Launch the DID desktop application."""
    if not extras.has_gui():
        print(extras.GUI_INSTALL)
        sys.exit(1)
    try:
        from gdid.main import main
    except ImportError:
        print(extras.GUI_INSTALL)
        sys.exit(1)
    main()


gui_cmd = command(
    name="gui",
    help="Launch the DID desktop review application. Requires extra did[gui].",
    callback=gui,
)
