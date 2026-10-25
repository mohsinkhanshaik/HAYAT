from app.style import naturalize


def test_conversation_style_removes_decorative_markdown():
    text = "# Answer\n\n**Main point:** hello\n* first\n* second"
    assert naturalize(text) == "Answer\n\nMain point: hello\n- first\n- second"


def test_conversation_style_preserves_fenced_code():
    text = "## Example\n```python\n# comment\nvalue = **broken**\n```"
    rendered = naturalize(text)
    assert rendered.startswith("Example")
    assert "# comment" in rendered
    assert "value = **broken**" in rendered


def test_conversation_style_does_not_remove_natural_emoji():
    assert naturalize("Nice work 🙂") == "Nice work 🙂"
