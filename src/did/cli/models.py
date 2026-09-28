"""Language-model install command."""

import sys

from treeparse import argument, command, option

from ..core.anonymizer import SPACY_MODELS
from ..core.model_install import ModelInstallError, ensure_spacy_models


def models(languages, profile):
    """Install the spaCy models detection needs (only those missing)."""
    try:
        installed = ensure_spacy_models(profile, languages or None, progress=print)
    except (ModelInstallError, ValueError) as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    if not installed:
        print("All required spaCy models are already installed.")


models_cmd = command(
    name="models",
    help=(
        "Install the spaCy language models entity detection needs. Only missing "
        "models are downloaded; no other package is changed."
    ),
    callback=models,
    arguments=[
        argument(
            name="languages",
            arg_type=str,
            nargs="*",
            help="Language codes (da, en, sv); default all. English is always included.",
            sort_key=0,
        ),
    ],
    options=[
        option(
            flags=["--profile", "-p"],
            arg_type=str,
            default="thorough",
            choices=sorted(SPACY_MODELS),
            help="thorough (large models) or balanced (small models).",
            sort_key=0,
        ),
    ],
)
