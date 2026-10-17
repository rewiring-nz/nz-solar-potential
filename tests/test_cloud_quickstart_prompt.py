"""Exercise the human confirmation gate using a pseudo-terminal."""

from __future__ import annotations

import os
import pty
import select
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "quickstart-cloud.sh"


def _run_with_answer(
    answer: str, *, install_gcloud: bool = False
) -> tuple[int, str, Path]:
    temporary = tempfile.TemporaryDirectory(prefix="cloud-prompt-test-")
    temp_path = Path(temporary.name)
    bin_dir = temp_path / "bin"
    log_dir = temp_path / "logs"
    bin_dir.mkdir()
    log_dir.mkdir()
    calls_file = temp_path / "gcloud-calls"
    mock_gcloud = bin_dir / "gcloud"
    gcloud_script = (
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$MOCK_GCLOUD_CALLS\"\n"
        "case \"$1 $2\" in\n"
        "  'auth list') echo mock-user@example.invalid ;;\n"
        "  'compute instances') echo 'ERROR: permission denied' >&2; exit 1 ;;\n"
        "  *) echo \"unexpected gcloud command: $*\" >&2; exit 2 ;;\n"
        "esac\n"
    )
    if not install_gcloud:
        mock_gcloud.write_text(gcloud_script, encoding="utf-8")
        mock_gcloud.chmod(0o755)
    else:
        mock_brew = bin_dir / "brew"
        mock_brew.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$MOCK_BREW_CALLS\"\n"
            "test \"$*\" = 'install --cask gcloud-cli' || exit 2\n"
            "cat > \"$MOCK_BIN/gcloud\" <<'GCLOUD'\n"
            f"{gcloud_script}"
            "GCLOUD\n"
            "chmod +x \"$MOCK_BIN/gcloud\"\n",
            encoding="utf-8",
        )
        mock_brew.chmod(0o755)

    pid, master_fd = pty.fork()
    if pid == 0:
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join(
            (str(bin_dir), "/usr/bin", "/bin", "/usr/sbin", "/sbin")
        )
        environment["CLOUD_QUICKSTART_LOG_DIR"] = str(log_dir)
        environment["MOCK_GCLOUD_CALLS"] = str(calls_file)
        environment["MOCK_BREW_CALLS"] = str(temp_path / "brew-calls")
        environment["MOCK_BIN"] = str(bin_dir)
        args = [str(SCRIPT), "--check"] if install_gcloud else [str(SCRIPT)]
        os.execve(SCRIPT, args, environment)

    output = bytearray()
    deadline = time.monotonic() + 15
    prompt = (
        b"Google Cloud CLI (gcloud) is missing. Install it with Homebrew? (yes/NO)"
        if install_gcloud
        else b"Continue? (yes/NO)"
    )
    while prompt not in output and time.monotonic() < deadline:
        ready, _, _ = select.select([master_fd], [], [], 0.2)
        if ready:
            try:
                output.extend(os.read(master_fd, 4096))
            except OSError:
                break
    if prompt not in output:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(master_fd)
        temporary.cleanup()
        raise AssertionError(f"cloud confirmation prompt not reached; output: {output!r}")

    os.write(master_fd, answer.encode("utf-8") + b"\n")
    deadline = time.monotonic() + 15
    status = None
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master_fd], [], [], 0.2)
        if ready:
            try:
                output.extend(os.read(master_fd, 4096))
            except OSError:
                pass
        waited, wait_status = os.waitpid(pid, os.WNOHANG)
        if waited:
            status = os.waitstatus_to_exitcode(wait_status)
            break
    if status is None:
        os.kill(pid, 9)
        _, wait_status = os.waitpid(pid, 0)
        status = os.waitstatus_to_exitcode(wait_status)
        os.close(master_fd)
        temporary.cleanup()
        raise AssertionError("cloud quickstart did not exit after confirmation")
    ready, _, _ = select.select([master_fd], [], [], 0.2)
    if ready:
        try:
            output.extend(os.read(master_fd, 4096))
        except OSError:
            pass
    os.close(master_fd)
    # Keep the temporary directory alive for callers to inspect the mock record.
    _TEMPORARIES.append(temporary)
    return status, output.decode("utf-8", errors="replace"), calls_file


_TEMPORARIES: list[tempfile.TemporaryDirectory[str]] = []


def test_enter_declines_before_any_gcloud_command() -> None:
    status, output, calls_file = _run_with_answer("")
    assert status == 0, output
    assert "Estimated cloud cost: NZD $3.00" in output
    assert "Human confirmation: declined. No cloud action was started." in output
    assert not calls_file.exists()


def test_yes_approves_then_runs_preflight() -> None:
    status, output, calls_file = _run_with_answer("yes")
    assert status == 1, output
    assert "Human confirmation: approved." in output
    assert "Confirm your account is in solarpotential@googlegroups.com" in output
    auth_command = calls_file.read_text(encoding="utf-8").splitlines()[0]
    assert auth_command.startswith("auth list --filter=status:ACTIVE")
    assert "--format=value(account)" in auth_command


def test_missing_gcloud_prompts_before_homebrew_install() -> None:
    status, output, calls_file = _run_with_answer("yes", install_gcloud=True)
    assert status == 1, output
    assert "brew install --cask gcloud-cli" in output
    assert "Confirm your account is in solarpotential@googlegroups.com" in output
    assert (calls_file.parent / "brew-calls").read_text(
        encoding="utf-8"
    ).strip() == "install --cask gcloud-cli"
    assert calls_file.read_text(encoding="utf-8").splitlines()[0].startswith(
        "auth list --filter=status:ACTIVE"
    )


def test_missing_gcloud_install_declines_by_default() -> None:
    status, output, calls_file = _run_with_answer("", install_gcloud=True)
    assert status == 1, output
    assert "gcloud installation declined; no package was installed." in output
    assert not (calls_file.parent / "brew-calls").exists()
    assert not calls_file.exists()


if __name__ == "__main__":
    test_enter_declines_before_any_gcloud_command()
    test_yes_approves_then_runs_preflight()
    test_missing_gcloud_prompts_before_homebrew_install()
    test_missing_gcloud_install_declines_by_default()
    for temporary in _TEMPORARIES:
        temporary.cleanup()
    print("cloud quickstart confirmation tests passed")
