"""Paged image gallery. Workers decode previews; Tk only paints completed results."""
import queue
import threading
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk


class ImageGallery(tk.Frame):
    def __init__(self, parent, palette, runtime, compose):
        super().__init__(parent, bg=palette.BLACK)
        self.palette, self.runtime, self.compose = palette, runtime, compose
        self.kind = "gallery"
        self.page, self._pending, self._generation = 1, False, 0
        self._results = queue.Queue(maxsize=1)
        self.query = StringVar(self)
        self.status = StringVar(self, "Generated images and images you have attached")
        self.photos = []
        top = ttk.Frame(self)
        top.pack(fill="x", pady=8)
        entry = ttk.Entry(top, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda event: self.search())
        ttk.Button(top, text="Search images", command=self.search).pack(side="left", padx=5)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left")
        self.canvas = tk.Canvas(self, bg=palette.BLACK, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.cards = tk.Frame(self.canvas, bg=palette.BLACK)
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.cards)
        self.cards.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=5)
        self.previous = ttk.Button(bottom, text="Previous", command=lambda: self.change(-1))
        self.previous.pack(side="left")
        self.next = ttk.Button(bottom, text="Next", command=lambda: self.change(1))
        self.next.pack(side="right")
        ttk.Label(bottom, textvariable=self.status, wraplength=420).pack(fill="x")

    def search(self):
        self.page = 1
        self.refresh()

    def change(self, direction):
        if not self._pending:
            self.page = max(1, self.page + direction)
            self.refresh()

    def refresh(self):
        self._generation += 1
        if self.runtime.authority.privacy_mode:
            self.clear()
            self.status.set("Gallery hidden in privacy mode")
            return
        if self._pending:
            return
        self._pending = True
        generation, query, page = self._generation, self.query.get(), self.page
        self.status.set("Loading image previews…")
        def load():
            try:
                result = self.runtime.registry.execute("image_gallery", {"query": query, "page": page})
                if not result.success:
                    raise ValueError(result.error or "Image access unavailable")
                payload = result.output
                for row in payload["images"]:
                    if self.runtime.authority.privacy_mode or generation != self._generation:
                        break
                    if row.get("missing"):
                        row["preview"], row["preview_error"] = None, "File no longer exists"
                        continue
                    preview = self.runtime.registry.execute("image_preview", {"path": row["path"]})
                    row["preview"] = preview.output.get("png") if preview.success else None
                    row["preview_error"] = preview.error if not preview.success else ""
            except Exception as exc:
                payload = {"error": str(exc)}
            self._results.put((generation, payload))
        threading.Thread(target=load, name="hayat-gallery", daemon=True).start()

    def clear(self):
        for child in self.cards.winfo_children():
            child.destroy()
        self.photos.clear()

    def destroy(self):
        # Invalidate an in-flight page so it stops before opening another file.
        self._generation += 1
        self.photos.clear()
        super().destroy()

    def drain(self):
        if self.runtime.authority.privacy_mode and self.cards.winfo_children():
            self.clear()
            self.status.set("Gallery hidden in privacy mode")
        try:
            generation, payload = self._results.get_nowait()
        except queue.Empty:
            return
        self._pending = False
        if generation != self._generation:
            self.refresh()
            return
        self.clear()
        if self.runtime.authority.privacy_mode:
            self.status.set("Gallery hidden in privacy mode")
            return
        if "error" in payload:
            self.status.set(payload["error"])
            return
        for i, row in enumerate(payload["images"]):
            card = tk.Frame(self.cards, bg=self.palette.PANEL_2, padx=10, pady=10)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=5, pady=5)
            self.cards.columnconfigure(i % 2, weight=1)
            if row.get("preview"):
                photo = tk.PhotoImage(master=self, data=row["preview"])
                factor = max(1, (photo.width() + 239) // 240)
                photo = photo.subsample(factor)
                self.photos.append(photo)
                tk.Label(card, image=photo, bg=self.palette.PANEL_2).pack()
            else:
                reason = "File no longer exists" if row.get("missing") else "Preview unavailable"
                tk.Label(card, text=reason, bg=self.palette.PANEL_2, fg=self.palette.MUTED).pack()
            from pathlib import Path
            tk.Label(card, text=f"{row['origin']} · {Path(row['path']).name}", wraplength=235,
                     bg=self.palette.PANEL_2, fg=self.palette.WHITE).pack(pady=5)
            tk.Label(card, text=str(row.get("prompt") or "")[:180], wraplength=235,
                     bg=self.palette.PANEL_2, fg=self.palette.MUTED).pack()
            ttk.Button(card, text="Discuss in chat", command=lambda path=row["path"]:
                       self.compose(f"Please analyze this image: {path}")).pack(pady=5)
        self.previous.configure(state="normal" if self.page > 1 else "disabled")
        self.next.configure(state="normal" if payload["has_more"] else "disabled")
        self.status.set(f"Page {self.page} · {len(payload['images'])} images" if payload["images"] else "No matching images")
