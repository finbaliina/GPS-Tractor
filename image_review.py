from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from gps_location import Position
from json_io import read_json, write_json
from settings import settings


# Fixed widget layout values. These are cosmetic rather than model/image tuning.
WINDOW_PADDING_PX = 12
HEADER_BOTTOM_PADDING_PX = 8
ENTRY_WIDTH_CHARACTERS = 40
ENTRY_VERTICAL_PADDING_PX = 10
BUTTON_HORIZONTAL_PADDING_PX = 5


def review_image(image_path: Path, metadata_path: Path, position: Position) -> bool:
    """Show the image, ask for an area name, and return True if accepted."""
    review_settings = settings.image_review
    if not review_settings.enabled:
        return True

    import tkinter as tk
    from tkinter import messagebox, ttk

    from PIL import Image, ImageTk

    review_result = {"confirmed": False}
    window = tk.Tk()
    window.title("Confirm satellite image")

    content_frame = ttk.Frame(window, padding=WINDOW_PADDING_PX)
    content_frame.pack(fill="both", expand=True)

    ttk.Label(
        content_frame,
        text=f"{position.latitude:.6f}, {position.longitude:.6f}",
    ).pack(pady=(0, HEADER_BOTTOM_PADDING_PX))

    with Image.open(image_path) as source_image:
        preview_image = source_image.convert("RGB")
        preview_image.thumbnail(
            (
                review_settings.preview_width_px,
                review_settings.preview_height_px,
            ),
            Image.Resampling.LANCZOS,
        )

    tkinter_image = ImageTk.PhotoImage(preview_image)
    image_label = ttk.Label(content_frame, image=tkinter_image)
    image_label.image = tkinter_image
    image_label.pack()

    area_name = tk.StringVar()
    name_entry = ttk.Entry(
        content_frame,
        textvariable=area_name,
        width=ENTRY_WIDTH_CHARACTERS,
    )
    name_entry.pack(pady=ENTRY_VERTICAL_PADDING_PX)
    name_entry.focus_set()

    def finish_review(confirmed: bool) -> None:
        confirmed_area_name = area_name.get().strip() if confirmed else None
        if confirmed and not confirmed_area_name:
            messagebox.showwarning("Area name", "Please enter an area name.")
            return

        metadata = read_json(metadata_path)
        metadata["review"] = {
            "confirmed": confirmed,
            "area_name": confirmed_area_name,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(metadata_path, metadata)

        review_result["confirmed"] = confirmed
        window.destroy()

    button_frame = ttk.Frame(content_frame)
    button_frame.pack()
    ttk.Button(
        button_frame,
        text="Confirm",
        command=lambda: finish_review(True),
    ).pack(side="left", padx=BUTTON_HORIZONTAL_PADDING_PX)
    ttk.Button(
        button_frame,
        text="Wrong image",
        command=lambda: finish_review(False),
    ).pack(side="left", padx=BUTTON_HORIZONTAL_PADDING_PX)

    window.mainloop()
    return review_result["confirmed"]
