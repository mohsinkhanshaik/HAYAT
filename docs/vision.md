# Vision and screen understanding

## Beginner

Vision lets a configured model describe a screenshot or explicit image. It can be wrong, so computer actions must be observed again and verified.

Use **Add file → Images** in chat, drag an image into the conversation, or use
**Studios → Searches → Add image…**. The attachment reader extracts visible
text and requests a description from the configured vision model. It asks the
model to describe what is visible without identifying people from faces or
guessing private details or sensitive traits. No face-based internet lookup is
provided. See [research from the desktop](research.md#research-from-the-desktop).

```text
/vision data\artifacts\example.png Describe the visible interface.
/monitors status
/monitors capture 2
```

## Intermediate

The priority order is deterministic structure first: browser DOM/accessibility data, Windows UI Automation, OCR, screenshot pixels, then vision reasoning. Display capture uses real Win32 monitor geometry; active-window metadata identifies the monitor containing the window.

## Advanced

`app/perception/vision.py` routes images through the configured provider; `app/perception/displays.py` captures selected monitor bounds; camera observations reuse the same analyzer. If no compatible model is configured, analysis fails closed while screenshot capture remains usable. Vision output alone must not authorize a high-risk click.
