"""Unit tests for nodeiq.cli.platform_info."""

from nodeiq.cli import platform_info


def test_parse_pretty_name_extracts_value():
    raw_text = 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\nVERSION_ID="24.04"\n'
    assert platform_info._parse_pretty_name(raw_text) == "Ubuntu 24.04.4 LTS"


def test_parse_pretty_name_handles_single_quotes():
    raw_text = "PRETTY_NAME='Debian GNU/Linux 12 (bookworm)'\n"
    assert platform_info._parse_pretty_name(raw_text) == "Debian GNU/Linux 12 (bookworm)"


def test_parse_pretty_name_ignores_comments_and_blank_lines():
    raw_text = "# comment\n\nPRETTY_NAME=\"Fedora Linux 40\"\n"
    assert platform_info._parse_pretty_name(raw_text) == "Fedora Linux 40"


def test_parse_pretty_name_missing_returns_none():
    raw_text = 'NAME="Arch Linux"\n'
    assert platform_info._parse_pretty_name(raw_text) is None


def test_parse_pretty_name_empty_text_returns_none():
    assert platform_info._parse_pretty_name("") is None


def test_detect_platform_on_linux_with_os_release(monkeypatch):
    monkeypatch.setattr(platform_info.platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform_info.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        platform_info, "_linux_description", lambda: "Ubuntu 24.04.4 LTS"
    )

    result = platform_info.detect_platform()

    assert result == {
        "system": "Linux",
        "machine": "aarch64",
        "description": "Ubuntu 24.04.4 LTS",
        "is_linux": True,
    }


def test_detect_platform_on_linux_without_os_release_falls_back(monkeypatch):
    monkeypatch.setattr(platform_info.platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform_info.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform_info, "_linux_description", lambda: None)

    result = platform_info.detect_platform()

    assert result["description"] == "Linux"
    assert result["is_linux"] is True


def test_detect_platform_on_macos(monkeypatch):
    monkeypatch.setattr(platform_info.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform_info.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(platform_info.platform, "mac_ver", lambda: ("15.5", ("", "", ""), ""))

    result = platform_info.detect_platform()

    assert result["description"] == "macOS 15.5"
    assert result["machine"] == "arm64"
    assert result["is_linux"] is False


def test_detect_platform_on_windows(monkeypatch):
    monkeypatch.setattr(platform_info.platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform_info.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platform_info.platform, "release", lambda: "11")

    result = platform_info.detect_platform()

    assert result["description"] == "Windows 11"
    assert result["is_linux"] is False


def test_detect_platform_on_unknown_system_falls_back_to_system_name(monkeypatch):
    monkeypatch.setattr(platform_info.platform, "system", lambda: "SomeOtherOS")
    monkeypatch.setattr(platform_info.platform, "machine", lambda: "mystery")

    result = platform_info.detect_platform()

    assert result["description"] == "SomeOtherOS"
    assert result["is_linux"] is False


def test_linux_description_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "_OS_RELEASE_PATH", tmp_path / "does-not-exist")
    assert platform_info._linux_description() is None


def test_linux_description_reads_real_file(monkeypatch, tmp_path):
    os_release = tmp_path / "os-release"
    os_release.write_text('PRETTY_NAME="Test Linux 1.0"\n')
    monkeypatch.setattr(platform_info, "_OS_RELEASE_PATH", os_release)

    assert platform_info._linux_description() == "Test Linux 1.0"
