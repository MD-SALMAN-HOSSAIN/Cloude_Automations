"""GUI test: one-click connect, account auto-detection, preview and publish."""
from __future__ import annotations

import sys
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path

import customtkinter as ctk
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uploadtest
from core.config import Config
from core.ffmpeg_tools import make_test_video
from gui import main_window
from gui.main_window import App

OPENED = []
main_window.webbrowser.open = lambda url: OPENED.append(url)
main_window.messagebox.showinfo = lambda *a, **k: None
main_window.messagebox.showerror = lambda *a, **k: None
main_window.messagebox.showwarning = lambda *a, **k: None


def main():
    server = HTTPServer(("127.0.0.1", 0), uploadtest.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    cfg = Config()
    cfg.set("upload.base_url", f"http://127.0.0.1:{server.server_port}/api")
    cfg.set("upload.api_key", "gui-test-key")
    cfg.set("upload.user", "")
    cfg.set("upload.platforms", ["tiktok", "instagram"])
    cfg.set("upload.wait_for_status", False)
    cfg.save()

    work = Path(tempfile.mkdtemp(prefix="vcm_preview_"))
    video = make_test_video(work / "clip.mp4", seconds=4)
    Image.new("RGB", (1080, 1920), (30, 40, 60)).save(video.with_suffix(".jpg"))

    root = ctk.CTk()
    original_config = main_window.Config
    main_window.Config = lambda: cfg
    app = App(root)
    main_window.Config = original_config
    app.result = {"clips": [{"path": str(video), "title": "The amazing secret"}],
                  "output_dir": str(work)}
    app.up_preview_btn.configure(state="normal")
    app.up_publish_btn.configure(state="normal")

    # 1) Connect button -> browser link + auto-check
    root.after(300, app.connect_clicked)

    def after_connect():
        print("LINK OPENED:", OPENED[-1] if OPENED else "NONE")
        print("PROFILE:", app.up_user_var.get())
        print("STATUS:", app.up_status.cget("text"))
        assert OPENED and "connect?token=" in OPENED[-1]
        assert app.up_user_var.get().startswith("viralclip_")

        # Simulate the user finishing OAuth in the browser
        accounts = [
            {"platform": "tiktok", "handle": "mychannel", "display_name": "My Channel",
             "avatar": "", "reauth_required": False, "connected": True},
            {"platform": "instagram", "handle": "mygrid", "display_name": "My Grid",
             "avatar": "", "reauth_required": False, "connected": True},
        ]
        app.q.put(("watch_accounts", accounts))
        app._poll()
        print("CHECKBOXES:",
              [(p, b.cget("text"), bool(v.get())) for p, b, v in
               [(p, app.up_platform_boxes[p], app.up_platform_vars[p]) for p in ("tiktok", "instagram", "youtube")]])
        assert app.up_platform_boxes["tiktok"].cget("state") == "normal"
        assert app.up_platform_boxes["youtube"].cget("state") == "disabled"
        assert app.up_platform_vars["tiktok"].get()

        # 2) Preview window
        app.preview_clicked()
        preview = app.preview_window
        root.update()
        print("PREVIEW TITLE:", preview.title_label.cget("text"))
        print("PREVIEW CAPTION:", preview.caption_box.get("1.0", "end").strip())
        print("PREVIEW DEST:", preview.dest_label.cget("text").replace("\n", " | "))
        assert preview.title_label.cget("text") == "The amazing secret"
        assert "#shorts" in preview.caption_box.get("1.0", "end")
        assert "@mychannel" in preview.dest_label.cget("text")

        # 3) Edit caption + publish straight from the preview
        preview.caption_box.delete("1.0", "end")
        preview.caption_box.insert("1.0", "Edited caption #viral")
        preview.publish()

        def after_publish():
            print("PUBLISH LOG:", app.up_log.get("1.0", "end").strip().splitlines()[-1])
            print("PREVIEW CLOSED:", app.preview_window is None)
            assert "Edited caption #viral" in uploadtest.RECEIVED[-1]["body"]
            assert app.preview_window is None
            app.connecting = False
            root.destroy()
            server.shutdown()
            print("GUI CONNECT + PREVIEW TEST PASSED")

        root.after(2500, after_publish)

    root.after(2500, after_connect)
    root.mainloop()


if __name__ == "__main__":
    main()
