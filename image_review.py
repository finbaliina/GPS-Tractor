from datetime import datetime, timezone
from pathlib import Path
import json
import os

from gps_location import Position


def review_image(image_path: Path, metadata_path: Path, position: Position) -> bool:
    """Show the image, ask for an area name, and return True if accepted."""
    if not _env_bool("REVIEW_IMAGE", True):
        return True

    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import Image, ImageTk

    result = {"confirmed": False}
    root = tk.Tk()
    root.title("Confirm satellite image")

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text=f"{position.latitude:.6f}, {position.longitude:.6f}",
    ).pack(pady=(0, 8))

    with Image.open(image_path) as source:
        image = source.convert("RGB")
        image.thumbnail((1000, 650), Image.Resampling.LANCZOS)

    photo = ImageTk.PhotoImage(image)
    label = ttk.Label(frame, image=photo)
    label.image = photo
    label.pack()

    name = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=name, width=40)
    entry.pack(pady=10)
    entry.focus_set()

    def finish(confirmed):
        area_name = name.get().strip() if confirmed else None
        if confirmed and not area_name:
            messagebox.showwarning("Area name", "Please enter an area name.")
            return

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["review"] = {
            "confirmed": confirmed,
            "area_name": area_name,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

        result["confirmed"] = confirmed
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack()
    ttk.Button(buttons, text="Confirm", command=lambda: finish(True)).pack(side="left", padx=5)
    ttk.Button(buttons, text="Wrong image", command=lambda: finish(False)).pack(side="left", padx=5)
    
    root.mainloop()
    return result["confirmed"]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}
