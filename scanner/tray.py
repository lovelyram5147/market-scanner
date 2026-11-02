"""Optional system-tray icon. Needs:  pip install pystray pillow   (silently skipped if missing)."""
import webbrowser


def start(engine, url, on_quit):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[tray] pystray/pillow not installed, skipping tray icon (pip install -r requirements-tray.txt)")
        return None
    try:
        img = Image.new("RGB", (64, 64), "#0d1117")
        ImageDraw.Draw(img).ellipse((10, 10, 54, 54), fill="#3fb950")

        def toggle(icon, item):
            engine.running = not engine.running
            engine.next_run = 0

        def quit_(icon, item):
            icon.stop()
            on_quit()

        menu = pystray.Menu(
            pystray.MenuItem("Open panel", lambda i, it: webbrowser.open(url), default=True),
            pystray.MenuItem(lambda item: "Pause scanning" if engine.running else "Resume scanning", toggle),
            pystray.MenuItem("Quit", quit_),
        )
        icon = pystray.Icon("scalp_scanner", img, "Scalp Scanner", menu)
        icon.run_detached()
        return icon
    except Exception as e:
        print("[tray] could not start tray icon:", e)
        return None
