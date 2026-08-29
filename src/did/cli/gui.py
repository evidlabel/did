"""Desktop GUI command."""

from treeparse import command


def gui():
    """Launch the DID desktop application."""
    from gdid.main import main

    main()


gui_cmd = command(
    name="gui",
    help="Launch the DID desktop review application.",
    callback=gui,
)
