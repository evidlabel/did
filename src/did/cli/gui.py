"""Desktop GUI command."""

from treeparse import command, option

from gdid.web.server import serve_gui


def gui(
    port: int = 8791,
    browser: bool = False,
    headless: bool = False,
    agent: str | None = None,
):
    """Open the review window, or the browser when did-app is absent."""
    serve_gui(port=port or 8791, browser=browser, headless=headless, agent=agent)


gui_cmd = command(
    name="gui",
    help=(
        "Open the review window (did-app), or the browser with --browser. "
        "A second launch raises the running window."
    ),
    callback=gui,
    options=[
        option(
            flags=["--port", "-p"],
            arg_type=int,
            default=8791,
            help="Loopback port. The server binds 127.0.0.1 only.",
            sort_key=0,
        ),
        option(
            flags=["--browser"],
            flag=True,
            help="Open in the browser instead of did-app.",
            sort_key=1,
        ),
        option(
            flags=["--headless"],
            flag=True,
            help="Serve the page and open nothing.",
            sort_key=2,
        ),
        option(
            flags=["--agent"],
            arg_type=str,
            help=(
                "Command the Agent pane types into a new terminal "
                "(default $DID_AGENT, else opencode when installed). "
                "Empty starts a shell."
            ),
            sort_key=3,
        ),
    ],
)
