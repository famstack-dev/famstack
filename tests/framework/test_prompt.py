"""Prompt utilities: shared TUI primitives for hooks and installer."""

from unittest.mock import patch, call


class TestAsk:

    def test_returns_input(self):
        from stack.prompt import ask

        with patch("builtins.input", return_value="hello"):
            assert ask("Name") == "hello"

    def test_uses_default_on_empty(self):
        from stack.prompt import ask

        with patch("builtins.input", return_value=""):
            assert ask("Name", default="world") == "world"

    def test_strips_whitespace(self):
        from stack.prompt import ask

        with patch("builtins.input", return_value="  padded  "):
            assert ask("Name") == "padded"

    def test_retries_on_validation_failure(self):
        from stack.prompt import ask

        calls = iter(["", "ok"])
        with patch("builtins.input", side_effect=calls):
            result = ask("Name", validate=lambda v: "required" if not v else None)
            assert result == "ok"

    def test_returns_none_on_eof(self):
        from stack.prompt import ask

        with patch("builtins.input", side_effect=EOFError):
            assert ask("Name") is None

    def test_returns_none_on_interrupt(self):
        from stack.prompt import ask

        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert ask("Name") is None

    def test_prompt_has_chevron(self):
        from stack.prompt import ask

        with patch("builtins.input", return_value="x") as m:
            ask("Name")
            prompt_str = m.call_args[0][0]
            assert "\u203a" in prompt_str

    def test_prompt_shows_default_in_brackets(self):
        from stack.prompt import ask

        with patch("builtins.input", return_value="") as m:
            ask("Name", default="world")
            prompt_str = m.call_args[0][0]
            assert "world" in prompt_str


class TestConfirm:

    def test_yes(self):
        from stack.prompt import confirm

        with patch("builtins.input", return_value="y"):
            assert confirm("Sure?") is True

    def test_no(self):
        from stack.prompt import confirm

        with patch("builtins.input", return_value="n"):
            assert confirm("Sure?") is False

    def test_default_true(self):
        from stack.prompt import confirm

        with patch("builtins.input", return_value=""):
            assert confirm("Sure?", default=True) is True

    def test_default_false(self):
        from stack.prompt import confirm

        with patch("builtins.input", return_value=""):
            assert confirm("Sure?", default=False) is False

    def test_returns_false_on_eof(self):
        from stack.prompt import confirm

        with patch("builtins.input", side_effect=EOFError):
            assert confirm("Sure?") is False

    def test_prompt_has_question_mark(self):
        from stack.prompt import confirm

        with patch("builtins.input", return_value="y") as m:
            confirm("Sure?")
            prompt_str = m.call_args[0][0]
            assert "?" in prompt_str


class TestSymbols:

    def test_done_shows_checkmark(self, capsys):
        from stack.prompt import done

        done("worked")
        captured = capsys.readouterr().out
        assert "\u2713" in captured
        assert "worked" in captured

    def test_warn_shows_warning(self, capsys):
        from stack.prompt import warn

        warn("careful")
        captured = capsys.readouterr().out
        assert "\u26a0" in captured
        assert "careful" in captured

    def test_error_shows_cross(self, capsys):
        from stack.prompt import error

        error("failed")
        captured = capsys.readouterr().out
        assert "\u2717" in captured
        assert "failed" in captured


class TestNewPrimitives:

    def test_rule_prints_horizontal_line(self, capsys):
        from stack.prompt import rule

        rule()
        captured = capsys.readouterr().out
        assert "\u2500" in captured

    def test_rule_custom_width(self, capsys):
        from stack.prompt import rule

        rule(width=30)
        captured = capsys.readouterr().out
        assert "\u2500" * 30 in captured

    def test_kv_shows_label_and_value(self, capsys):
        from stack.prompt import kv

        kv("Timezone", "Europe/Berlin")
        captured = capsys.readouterr().out
        assert "Timezone" in captured
        assert "Europe/Berlin" in captured

    def test_bullet_shows_marker_and_text(self, capsys):
        from stack.prompt import bullet

        bullet("First item")
        captured = capsys.readouterr().out
        assert "First item" in captured
        # indented with a bullet marker
        assert captured.strip().startswith("\u2022") or "\u2022" in captured


class TestHeadingVariants:

    def test_heading_contains_text(self, capsys):
        from stack.prompt import heading

        heading("Test Section")
        captured = capsys.readouterr().out
        assert "Test Section" in captured

    def test_heading_has_accent_line(self, capsys):
        from stack.prompt import heading

        heading("Test")
        captured = capsys.readouterr().out
        assert "\u2500" in captured

    def test_section_shows_name_and_description(self, capsys):
        from stack.prompt import section

        section("AI Engine", "Local LLM inference")
        captured = capsys.readouterr().out
        assert "AI Engine" in captured
        assert "Local LLM inference" in captured

    def test_section_works_without_description(self, capsys):
        from stack.prompt import section

        section("Setup")
        captured = capsys.readouterr().out
        assert "Setup" in captured

    def test_banner_shows_product(self, capsys):
        from stack.prompt import banner

        banner("famstack")
        captured = capsys.readouterr().out
        assert "famstack" in captured

    def test_banner_shows_subtitle(self, capsys):
        from stack.prompt import banner

        banner("famstack", "Your family server")
        captured = capsys.readouterr().out
        assert "famstack" in captured
        assert "Your family server" in captured


class TestOutputHelpers:

    def test_out_indents(self, capsys):
        from stack.prompt import out

        out("hello")
        captured = capsys.readouterr().out
        assert captured.startswith("  ")
        assert "hello" in captured


class TestNoColor:

    def test_no_color_env_disables_escapes(self, capsys, monkeypatch):
        import importlib
        import stack.prompt as mod

        monkeypatch.setenv("NO_COLOR", "1")
        importlib.reload(mod)
        try:
            assert mod.ORANGE == ""
            assert mod.GREEN == ""
            assert mod.RESET == ""
            mod.done("msg")
            captured = capsys.readouterr().out
            assert "\033" not in captured
        finally:
            monkeypatch.delenv("NO_COLOR", raising=False)
            importlib.reload(mod)
