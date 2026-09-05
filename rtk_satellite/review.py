from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def save_review_metadata(
    metadata_path: Path,
    confirmed: bool,
    area_name: str | None,
) -> None:
    """Add the user's image review to an existing capture metadata file."""
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["review"] = {
        "confirmed": confirmed,
        "area_name": area_name.strip() if area_name else None,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def review_capture(
    image_path: Path,
    metadata_path: Path,
    latitude: float,
    longitude: float,
) -> tuple[bool, str | None]:
    """Display a capture and ask the user to confirm and name the area."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk

        from PIL import Image, ImageTk
    except ImportError as error:
        raise RuntimeError(
            "The review window needs Pillow and Tkinter. Install requirements.txt; "
            "on Raspberry Pi OS also run: sudo apt install python3-tk"
        ) from error

    result: dict[str, bool | str | None] = {
        "confirmed": False,
        "area_name": None,
        "reviewed": False,
    }

    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise RuntimeError(
            "Could not open the review window. Run this from a graphical desktop, "
            "or add --no-review when running without a screen."
        ) from error
    root.title("Confirm satellite image")
    root.minsize(720, 600)

    main = ttk.Frame(root, padding=16)
    main.pack(fill="both", expand=True)

    ttk.Label(
        main,
        text="Is this the correct area?",
        font=("Segoe UI", 18, "bold"),
    ).pack(pady=(0, 6))
    ttk.Label(
        main,
        text=f"Latitude {latitude:.8f}    Longitude {longitude:.8f}",
    ).pack(pady=(0, 12))

    try:
        with Image.open(image_path) as original:
            display_image = original.convert("RGB")
            max_width = min(1000, max(root.winfo_screenwidth() - 100, 600))
            max_height = min(650, max(root.winfo_screenheight() - 300, 350))
            display_image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    except OSError as error:
        root.destroy()
        raise RuntimeError(f"Could not open captured image: {error}") from error

    photo = ImageTk.PhotoImage(display_image)
    image_label = ttk.Label(main, image=photo)
    image_label.image = photo
    image_label.pack(fill="both", expand=True, pady=(0, 14))

    form = ttk.Frame(main)
    form.pack(fill="x")
    ttk.Label(form, text="Area name:").pack(side="left", padx=(0, 8))
    area_name = tk.StringVar()
    entry = ttk.Entry(form, textvariable=area_name, font=("Segoe UI", 12))
    entry.pack(side="left", fill="x", expand=True)
    entry.focus_set()

    buttons = ttk.Frame(main)
    buttons.pack(pady=(14, 0))

    def confirm() -> None:
        name = area_name.get().strip()
        if not name:
            messagebox.showwarning("Area name required", "Please enter a name for the area.")
            entry.focus_set()
            return

        save_review_metadata(metadata_path, confirmed=True, area_name=name)
        result.update(confirmed=True, area_name=name, reviewed=True)
        root.destroy()

    def reject() -> None:
        save_review_metadata(metadata_path, confirmed=False, area_name=None)
        result.update(confirmed=False, area_name=None, reviewed=True)
        root.destroy()

    def close_window() -> None:
        if messagebox.askyesno(
            "Close without confirming?",
            "Close the review window and leave this image unconfirmed?",
        ):
            root.destroy()

    ttk.Button(buttons, text="Image is wrong", command=reject).pack(side="left", padx=6)
    ttk.Button(buttons, text="Confirm and save", command=confirm).pack(side="left", padx=6)

    root.bind("<Return>", lambda _event: confirm())
    root.protocol("WM_DELETE_WINDOW", close_window)
    root.mainloop()

    return bool(result["confirmed"]), (
        str(result["area_name"]) if result["area_name"] is not None else None
    )
