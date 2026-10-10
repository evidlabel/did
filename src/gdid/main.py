"""Entry point for the gdid GUI."""


def main():
    from gdid.web.server import serve_gui

    serve_gui()


if __name__ == "__main__":
    main()
