from typer.testing import CliRunner

from open_revisit import __version__
from open_revisit.cli import app


def test_version_option() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
