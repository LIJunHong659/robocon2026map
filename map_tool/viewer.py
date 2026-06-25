from __future__ import annotations

import argparse
import json
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .geometry import Point, bbox, distance, fmt_num, point_in_polygon, polygon_centroid, segment_distance
from .model import MapDocument, MapObject


@dataclass(slots=True)
class SelectionHit:
    obj: MapObject
    rank: int
    distance_px: float
    nearest_vertex: tuple[int, Point, float] | None


class MapToolApp(tk.Tk):
    def __init__(self, initial_path: str | None = None) -> None:
        super().__init__()
        self.title("Robocon Map Parameter Tool")
        self.geometry("1540x920")
        self.minsize(1240, 760)

        self.doc: MapDocument | None = None
        self.current_path: Path | None = None
        self.selected_id: str | None = None
        self.selected_vertex_index: int | None = None
        self.hover_world: Point | None = None
        self.last_click_world: Point | None = None

        self.scale = 1.0
        self.center_x = 0.0
        self.center_y = 0.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        self.search_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="default")
        self.status_var = tk.StringVar(value="Ready")

        self._drag_start: tuple[int, int] | None = None
        self._drag_pan_start: tuple[float, float] | None = None
        self._fit_pending = False

        self._build_ui()
        self._bind_events()

        default_path = Path(initial_path) if initial_path else Path(__file__).resolve().parents[1] / "maps" / "robocon_2026_template.json"
        self.after(0, lambda: self.open_map(default_path))

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(side="top", fill="x")

        ttk.Button(toolbar, text="Open", command=self.open_dialog).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Reload", command=self.reload_map).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Fit", command=self.fit_to_document).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Zoom +", command=lambda: self.zoom_at_canvas_center(1.15)).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Zoom -", command=lambda: self.zoom_at_canvas_center(1 / 1.15)).pack(side="left", padx=(0, 14))
        ttk.Button(toolbar, text="Copy Object", command=self.copy_selected_object).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Copy Vertex", command=self.copy_selected_vertex).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Copy Click", command=self.copy_last_click).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Center", command=self.center_on_selection).pack(side="left", padx=(0, 6))
        ttk.Label(toolbar, text="Mode").pack(side="left", padx=(14, 6))
        self.mode_combo = ttk.Combobox(toolbar, textvariable=self.mode_var, state="readonly", width=12)
        self.mode_combo.pack(side="left")

        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 10))

        self.left_frame = ttk.Frame(main, padding=10)
        self.canvas_frame = ttk.Frame(main)
        self.right_frame = ttk.Frame(main, padding=10)
        main.add(self.left_frame, weight=0)
        main.add(self.canvas_frame, weight=1)
        main.add(self.right_frame, weight=0)

        ttk.Label(self.left_frame, text="Search").pack(anchor="w")
        search_entry = ttk.Entry(self.left_frame, textvariable=self.search_var)
        search_entry.pack(fill="x", pady=(4, 10))
        search_entry.focus_set()

        tree_container = ttk.Frame(self.left_frame)
        tree_container.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_container, columns=("id", "type", "tags"), show="tree headings", selectmode="browse", height=20)
        self.tree.heading("#0", text="Name")
        self.tree.heading("id", text="ID")
        self.tree.heading("type", text="Type")
        self.tree.heading("tags", text="Tags")
        self.tree.column("#0", width=160, anchor="w")
        self.tree.column("id", width=130, anchor="w")
        self.tree.column("type", width=90, anchor="w")
        self.tree.column("tags", width=180, anchor="w")
        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        ttk.Label(self.right_frame, text="Selection").pack(anchor="w")
        self.detail_text = tk.Text(self.right_frame, width=48, height=28, wrap="word")
        self.detail_text.pack(fill="both", expand=True, pady=(6, 10))
        self.detail_text.configure(state="disabled")

        self.cursor_label = ttk.Label(self.right_frame, text="Cursor: -")
        self.cursor_label.pack(anchor="w", pady=(0, 8))
        self.click_label = ttk.Label(self.right_frame, text="Click: -")
        self.click_label.pack(anchor="w", pady=(0, 8))

        canvas_bg = "#0f172a"
        self.canvas = tk.Canvas(self.canvas_frame, bg=canvas_bg, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.status = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 4))
        self.status.pack(side="bottom", fill="x")

        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        self.mode_var.trace_add("write", lambda *_: self._on_mode_change())
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_mode_change())

    def _bind_events(self) -> None:
        self.canvas.bind("<Configure>", lambda event: self.redraw())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _event: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<ButtonPress-2>", self._start_pan)
        self.canvas.bind("<B2-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-2>", self._end_pan)
        self.canvas.bind("<ButtonPress-3>", self._start_pan)
        self.canvas.bind("<B3-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-3>", self._end_pan)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", self._on_mouse_wheel)
        self.canvas.bind("<Button-5>", self._on_mouse_wheel)
        self.bind("<Escape>", lambda _event: self._clear_selection())
        self.bind("<Control-o>", lambda _event: self.open_dialog())
        self.bind("<Control-r>", lambda _event: self.reload_map())
        self.bind("<Control-f>", lambda _event: self.search_var.set(""))

    def open_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Open map file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.open_map(Path(path))

    def reload_map(self) -> None:
        if self.current_path is not None:
            self.open_map(self.current_path)

    def open_map(self, path: Path) -> None:
        try:
            doc = MapDocument.load(path)
        except Exception as exc:
            messagebox.showerror("Load failed", f"Failed to load map:\n{exc}")
            return

        self.doc = doc
        self.current_path = Path(path).expanduser().resolve()
        self.selected_id = None
        self.selected_vertex_index = None
        self.last_click_world = None
        self.hover_world = None
        self.mode_combo.configure(values=doc.display_mode_names())
        self.mode_var.set(doc.default_display_mode)
        self._fit_pending = True
        self._refresh_title()
        self.refresh_tree()
        self.after_idle(self.fit_to_document)
        self.redraw()
        self._update_details()

    def _refresh_title(self) -> None:
        if self.doc is None:
            self.title("Robocon Map Parameter Tool")
            return
        suffix = f" - {self.current_path.name}" if self.current_path else ""
        self.title(f"Robocon Map Parameter Tool - {self.doc.name}{suffix}")

    def refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.doc is None:
            return
        query = self.search_var.get().strip().lower()
        objects = self.doc.filtered_objects(query)
        for obj in objects:
            self.tree.insert(
                "",
                "end",
                iid=obj.id,
                text=obj.name,
                values=(obj.id, obj.type, ", ".join(obj.tags)),
            )
        if self.selected_id and self.tree.exists(self.selected_id):
            self.tree.selection_set(self.selected_id)
            self.tree.see(self.selected_id)

    def _on_tree_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.selected_id = selection[0]
        self.selected_vertex_index = None
        self.redraw()
        self._update_details()

    def selected_object(self) -> MapObject | None:
        if self.doc is None or self.selected_id is None:
            return None
        return self.doc.object_by_id(self.selected_id)

    def active_mode_name(self) -> str:
        if self.doc is None:
            return self.mode_var.get() or "default"
        mode_name = self.mode_var.get()
        if mode_name in self.doc.display_modes:
            return mode_name
        return self.doc.default_display_mode

    def active_origin_world(self) -> Point:
        if self.doc is None:
            return 0.0, 0.0
        return self.doc.mode_origin_world(self.active_mode_name())

    def _display_point(self, point: Point) -> Point:
        if self.doc is None:
            return point
        return self.doc.to_mode_display_point(point, self.active_mode_name())

    def _on_mode_change(self) -> None:
        if self.doc is None:
            return
        if self.hover_world is not None:
            self._set_hover(self.hover_world)
        if self.last_click_world is None:
            self.click_label.configure(text="Click: -")
        else:
            dx, dy = self._display_point(self.last_click_world)
            self.click_label.configure(text=f"Click: ({fmt_num(dx)}, {fmt_num(dy)})")
        self.redraw()
        self._update_details()

    def _clear_selection(self) -> None:
        self.selected_id = None
        self.selected_vertex_index = None
        self.tree.selection_remove(self.tree.selection())
        self.redraw()
        self._update_details()

    def _set_hover(self, point: Point | None) -> None:
        self.hover_world = point
        if point is None:
            self.cursor_label.configure(text="Cursor: -")
        else:
            dx, dy = self._display_point(point)
            self.cursor_label.configure(text=f"Cursor: ({fmt_num(dx)}, {fmt_num(dy)})")

    def _on_motion(self, event: tk.Event) -> None:
        world = self.canvas_to_world(event.x, event.y)
        self._set_hover(world)
        self.status_var.set(self._status_text(world))

    def _on_canvas_click(self, event: tk.Event) -> None:
        world = self.canvas_to_world(event.x, event.y)
        self.last_click_world = world
        dx, dy = self._display_point(world)
        self.click_label.configure(text=f"Click: ({fmt_num(dx)}, {fmt_num(dy)})")

        hit = self._pick_object(world)
        if hit is not None:
            self.selected_id = hit.obj.id
            self.selected_vertex_index = hit.nearest_vertex[0] if hit.nearest_vertex else None
            if self.tree.exists(hit.obj.id):
                self.tree.selection_set(hit.obj.id)
                self.tree.see(hit.obj.id)
        self.redraw()
        self._update_details()
        self.status_var.set(self._status_text(world))

    def _start_pan(self, event: tk.Event) -> None:
        self._drag_start = (event.x, event.y)
        self._drag_pan_start = (self.pan_x, self.pan_y)

    def _drag_pan(self, event: tk.Event) -> None:
        if self._drag_start is None or self._drag_pan_start is None:
            return
        start_x, start_y = self._drag_start
        pan_x, pan_y = self._drag_pan_start
        self.pan_x = pan_x + (event.x - start_x)
        self.pan_y = pan_y + (event.y - start_y)
        self.redraw()

    def _end_pan(self, _event: tk.Event) -> None:
        self._drag_start = None
        self._drag_pan_start = None

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        if getattr(event, "num", None) == 5:
            factor = 1 / 1.12
        elif getattr(event, "num", None) == 4:
            factor = 1.12
        else:
            factor = 1.12 if event.delta > 0 else 1 / 1.12
        self.zoom_at(event.x, event.y, factor)

    def zoom_at_canvas_center(self, factor: float) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.zoom_at(width // 2, height // 2, factor)

    def zoom_at(self, canvas_x: int, canvas_y: int, factor: float) -> None:
        if factor <= 0:
            return
        world_x, world_y = self.canvas_to_world(canvas_x, canvas_y)
        self.scale = max(0.01, min(self.scale * factor, 200.0))
        self.pan_x = canvas_x - self.canvas.winfo_width() / 2 - (world_x - self.center_x) * self.scale
        self.pan_y = canvas_y - self.canvas.winfo_height() / 2 + (world_y - self.center_y) * self.scale
        self.redraw()

    def fit_to_document(self) -> None:
        if self.doc is None:
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        if width < 20 or height < 20:
            self.after(50, self.fit_to_document)
            return
        min_x, min_y, max_x, max_y = self.doc.bounds()
        span_x = max(1.0, max_x - min_x)
        span_y = max(1.0, max_y - min_y)
        margin = max(24, self.doc.view.padding_px)
        scale_x = (width - 2 * margin) / span_x if width > 2 * margin else width / span_x
        scale_y = (height - 2 * margin) / span_y if height > 2 * margin else height / span_y
        self.scale = max(0.01, min(scale_x, scale_y))
        self.center_x = (min_x + max_x) / 2.0
        self.center_y = (min_y + max_y) / 2.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._fit_pending = False
        self.redraw()

    def center_on_selection(self) -> None:
        obj = self.selected_object()
        if obj is None:
            return
        min_x, min_y, max_x, max_y = obj.bbox()
        self.center_x = (min_x + max_x) / 2.0
        self.center_y = (min_y + max_y) / 2.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.redraw()

    def copy_selected_object(self) -> None:
        obj = self.selected_object()
        if obj is None:
            self.status_var.set("Nothing selected")
            return
        payload = {
            "id": obj.id,
            "name": obj.name,
            "type": obj.type,
            "tags": obj.tags,
            "mode": self.active_mode_name(),
        }
        if obj.is_point:
            payload["x"], payload["y"] = self._display_point(obj.points[0])
        else:
            payload["points"] = [{"x": x, "y": y} for x, y in map(self._display_point, obj.points)]
        self._copy_text(json.dumps(payload, ensure_ascii=False, indent=2))
        self.status_var.set(f"Copied object {obj.id}")

    def copy_selected_vertex(self) -> None:
        obj = self.selected_object()
        if obj is None or not obj.points:
            self.status_var.set("Nothing selected")
            return
        index = self.selected_vertex_index
        if index is None or index >= len(obj.points):
            index = 0
        x, y = self._display_point(obj.points[index])
        payload = {
            "id": f"{obj.id}_p{index + 1}",
            "x": x,
            "y": y,
            "mode": self.active_mode_name(),
            "source_object": obj.id,
            "vertex_index": index + 1,
        }
        self._copy_text(json.dumps(payload, ensure_ascii=False, indent=2))
        self.status_var.set(f"Copied vertex from {obj.id}")

    def copy_last_click(self) -> None:
        if self.last_click_world is None:
            self.status_var.set("No clicked coordinate")
            return
        x, y = self._display_point(self.last_click_world)
        payload = {"mode": self.active_mode_name(), "x": x, "y": y}
        self._copy_text(json.dumps(payload, ensure_ascii=False, indent=2))
        self.status_var.set("Copied clicked coordinate")

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _status_text(self, world: Point | None) -> str:
        if self.doc is None:
            return "No map loaded"
        parts = [f"Map: {self.doc.name}"]
        if world is not None:
            dx, dy = self._display_point(world)
            parts.append(f"cursor=({fmt_num(dx)}, {fmt_num(dy)})")
        if self.selected_id:
            parts.append(f"selected={self.selected_id}")
        return " | ".join(parts)

    def _hit_sort_key(self, hit: SelectionHit) -> tuple[float, float, float]:
        area = hit.obj.area() if hit.obj.type == "polygon" else 0.0
        return hit.rank, hit.distance_px, area

    def _pick_object(self, world: Point) -> SelectionHit | None:
        if self.doc is None:
            return None
        best: SelectionHit | None = None
        threshold_px = 14.0
        for obj in self.doc.filtered_objects(self.search_var.get()):
            rank, dist_px, nearest_vertex = self._object_hit(obj, world)
            if dist_px > threshold_px:
                continue
            hit = SelectionHit(obj=obj, rank=rank, distance_px=dist_px, nearest_vertex=nearest_vertex)
            if best is None or self._hit_sort_key(hit) < self._hit_sort_key(best):
                best = hit
        return best

    def _object_hit(self, obj: MapObject, world: Point) -> tuple[int, float, tuple[int, Point, float] | None]:
        if obj.is_point:
            px_distance = distance(obj.points[0], world) * self.scale
            return 0, px_distance, obj.nearest_vertex(world)

        points = obj.geometry_points()
        nearest_vertex = obj.nearest_vertex(world)
        if obj.type == "polygon" and point_in_polygon(world, points):
            return 1, 0.0, nearest_vertex

        if len(points) >= 2:
            best = min(segment_distance(world, a, b) for a, b in zip(points, points[1:]))
            if obj.type == "polygon" and len(points) >= 3:
                best = min(best, segment_distance(world, points[-1], points[0]))
            return (2 if obj.type == "line" else 3), best * self.scale, nearest_vertex

        if points:
            return 3, distance(points[0], world) * self.scale, nearest_vertex
        return 9, 1e9, None

    def redraw(self) -> None:
        self.canvas.delete("all")
        if self.doc is None:
            self.canvas.create_text(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                text="Open a map file to begin",
                fill="#cbd5e1",
                font=("Segoe UI", 18),
            )
            return

        self._draw_grid()
        self._draw_objects()
        self._draw_axes()
        self._draw_selection()
        self._draw_probe()

    def _draw_grid(self) -> None:
        if self.doc is None or not self.doc.view.show_grid:
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        left_top = self.canvas_to_world(0, 0)
        right_bottom = self.canvas_to_world(width, height)
        min_x = min(left_top[0], right_bottom[0])
        max_x = max(left_top[0], right_bottom[0])
        min_y = min(left_top[1], right_bottom[1])
        max_y = max(left_top[1], right_bottom[1])

        step = max(1.0, self.doc.view.grid_step)
        while step * self.scale < 48.0:
            step *= 2.0

        grid_color = "#1f2937"
        label_color = "#64748b"
        x = (min_x // step) * step
        if x < min_x:
            x += step
        while x <= max_x:
            sx1, sy1 = self.world_to_canvas(x, min_y)
            sx2, sy2 = self.world_to_canvas(x, max_y)
            self.canvas.create_line(sx1, sy1, sx2, sy2, fill=grid_color)
            if int(round(x / step)) % 5 == 0:
                label = self._grid_label_for_vertical_line(x, min_y, max_y)
                if label is not None:
                    self.canvas.create_text(sx1 + 4, height - 10, text=label, fill=label_color, anchor="sw", font=("Segoe UI", 8))
            x += step

        y = (min_y // step) * step
        if y < min_y:
            y += step
        while y <= max_y:
            sx1, sy1 = self.world_to_canvas(min_x, y)
            sx2, sy2 = self.world_to_canvas(max_x, y)
            self.canvas.create_line(sx1, sy1, sx2, sy2, fill=grid_color)
            if int(round(y / step)) % 5 == 0:
                label = self._grid_label_for_horizontal_line(y, min_x, max_x)
                if label is not None:
                    self.canvas.create_text(8, sy1 - 2, text=label, fill=label_color, anchor="sw", font=("Segoe UI", 8))
            y += step

    def _grid_label_for_vertical_line(self, world_x: float, min_y: float, max_y: float) -> str | None:
        p1 = self._display_point((world_x, min_y))
        p2 = self._display_point((world_x, max_y))
        if abs(p1[0] - p2[0]) < 1e-6:
            return fmt_num(p1[0])
        if abs(p1[1] - p2[1]) < 1e-6:
            return fmt_num(p1[1])
        return None

    def _grid_label_for_horizontal_line(self, world_y: float, min_x: float, max_x: float) -> str | None:
        p1 = self._display_point((min_x, world_y))
        p2 = self._display_point((max_x, world_y))
        if abs(p1[0] - p2[0]) < 1e-6:
            return fmt_num(p1[0])
        if abs(p1[1] - p2[1]) < 1e-6:
            return fmt_num(p1[1])
        return None

    def _draw_axes(self) -> None:
        if self.doc is None or not self.doc.view.show_axes:
            return
        min_x, min_y, max_x, max_y = self.doc.bounds()
        axis_len = max(max_x - min_x, max_y - min_y) * 1.1
        origin = self.active_origin_world()
        mode_offset_x, mode_offset_y = self.doc.display_mode_offset(self.active_mode_name())
        x_start = self.doc.from_display_point((mode_offset_x - axis_len, mode_offset_y))
        x_end = self.doc.from_display_point((mode_offset_x + axis_len, mode_offset_y))
        y_start = self.doc.from_display_point((mode_offset_x, mode_offset_y - axis_len))
        y_end = self.doc.from_display_point((mode_offset_x, mode_offset_y + axis_len))

        sox, soy = self.world_to_canvas(origin[0], origin[1])
        sxx1, sxy1 = self.world_to_canvas(x_start[0], x_start[1])
        sxx2, sxy2 = self.world_to_canvas(x_end[0], x_end[1])
        self.canvas.create_line(sxx1, sxy1, sxx2, sxy2, fill="#ef4444", width=3, arrow="last")
        self.canvas.create_text(sxx2 - 8, sxy2 - 8, text="X", fill="#ef4444", anchor="se", font=("Segoe UI", 9, "bold"))

        syx1, syy1 = self.world_to_canvas(y_start[0], y_start[1])
        syx2, syy2 = self.world_to_canvas(y_end[0], y_end[1])
        self.canvas.create_line(syx1, syy1, syx2, syy2, fill="#38bdf8", width=3, arrow="last")
        anchor = "sw" if syx2 >= sox else "se"
        self.canvas.create_text(syx2 + (8 if anchor == "sw" else -8), syy2 - 8, text="Y", fill="#38bdf8", anchor=anchor, font=("Segoe UI", 9, "bold"))
        self.canvas.create_oval(sox - 5, soy - 5, sox + 5, soy + 5, outline="#f8fafc", fill="#0f172a", width=2)
        self.canvas.create_text(sox + 8, soy + 8, text="O", fill="#f8fafc", anchor="nw", font=("Segoe UI", 9, "bold"))

    def _draw_objects(self) -> None:
        if self.doc is None:
            return
        ordered = sorted(self.doc.objects, key=lambda obj: {"polygon": 0, "line": 1, "point": 2}.get(obj.type, 3))
        for obj in ordered:
            if obj.type == "polygon":
                self._draw_polygon(obj, selected=False)
            elif obj.type == "line":
                self._draw_line(obj, selected=False)
            else:
                self._draw_point(obj, selected=False)

    def _draw_selection(self) -> None:
        obj = self.selected_object()
        if obj is None:
            return
        if obj.type == "polygon":
            self._draw_polygon(obj, selected=True)
        elif obj.type == "line":
            self._draw_line(obj, selected=True)
        else:
            self._draw_point(obj, selected=True)
        if self.selected_vertex_index is not None and obj.points:
            index = min(self.selected_vertex_index, len(obj.points) - 1)
            x, y = obj.points[index]
            sx, sy = self.world_to_canvas(x, y)
            self.canvas.create_oval(sx - 8, sy - 8, sx + 8, sy + 8, outline="#f59e0b", width=3)

    def _draw_probe(self) -> None:
        if self.last_click_world is None:
            return
        x, y = self.last_click_world
        sx, sy = self.world_to_canvas(x, y)
        dx, dy = self._display_point((x, y))
        self.canvas.create_line(sx - 10, sy, sx + 10, sy, fill="#facc15", width=2)
        self.canvas.create_line(sx, sy - 10, sx, sy + 10, fill="#facc15", width=2)
        self.canvas.create_text(
            sx + 12,
            sy - 12,
            text=f"({fmt_num(dx)}, {fmt_num(dy)})",
            fill="#facc15",
            anchor="sw",
            font=("Segoe UI", 9),
        )

    def _draw_point(self, obj: MapObject, selected: bool) -> None:
        x, y = obj.points[0]
        sx, sy = self.world_to_canvas(x, y)
        radius = max(4.0, obj.radius * (1.35 if selected else 1.0))
        outline = "#f59e0b" if selected else obj.color
        fill = obj.color
        self.canvas.create_oval(sx - radius, sy - radius, sx + radius, sy + radius, outline=outline, fill=fill, width=2)
        if self.doc and self.doc.view.show_labels:
            self.canvas.create_text(sx + 10, sy - 10, text=obj.name, fill="#e2e8f0", anchor="sw", font=("Segoe UI", 9))

    def _draw_line(self, obj: MapObject, selected: bool) -> None:
        if len(obj.points) < 2:
            return
        coords: list[float] = []
        for x, y in obj.points:
            sx, sy = self.world_to_canvas(x, y)
            coords.extend([sx, sy])
        self.canvas.create_line(
            *coords,
            fill="#f59e0b" if selected else obj.color,
            width=max(2.0, obj.width + (1.5 if selected else 0.0)),
        )
        if self.doc and self.doc.view.show_labels:
            cx, cy = obj.center()
            sx, sy = self.world_to_canvas(cx, cy)
            self.canvas.create_text(sx, sy - 8, text=obj.name, fill="#e2e8f0", anchor="s", font=("Segoe UI", 9))

    def _draw_polygon(self, obj: MapObject, selected: bool) -> None:
        if len(obj.points) < 3:
            self._draw_line(obj, selected)
            return
        coords: list[float] = []
        for x, y in obj.points:
            sx, sy = self.world_to_canvas(x, y)
            coords.extend([sx, sy])
        outline = "#f59e0b" if selected else obj.color
        fill = obj.fill if obj.fill is not None else "#0f172a"
        if selected:
            fill = ""
        self.canvas.create_polygon(
            *coords,
            outline=outline,
            fill=fill,
            width=max(2.0, obj.width + (1.8 if selected else 0.0)),
        )
        if self.doc and self.doc.view.show_labels:
            cx, cy = obj.center()
            sx, sy = self.world_to_canvas(cx, cy)
            self.canvas.create_text(sx, sy, text=obj.name, fill="#e2e8f0", anchor="center", font=("Segoe UI", 9))

    def world_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        canvas_x = width / 2 + self.pan_x + (x - self.center_x) * self.scale
        canvas_y = height / 2 + self.pan_y - (y - self.center_y) * self.scale
        return canvas_x, canvas_y

    def canvas_to_world(self, x: float, y: float) -> Point:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        world_x = self.center_x + (x - width / 2 - self.pan_x) / self.scale
        world_y = self.center_y - (y - height / 2 - self.pan_y) / self.scale
        return world_x, world_y

    def _update_details(self) -> None:
        obj = self.selected_object()
        lines: list[str] = []
        if self.doc is None:
            lines.append("No map loaded.")
        else:
            lines.append(f"Map: {self.doc.name}")
            lines.append(f"ID: {self.doc.map_id}")
            lines.append(f"Unit: {self.doc.unit}")
            lines.append(f"Mode: {self.active_mode_name()}")
            ox, oy = self.active_origin_world()
            lines.append(f"Display Origin (world): ({fmt_num(ox)}, {fmt_num(oy)})")
            if self.doc.axes:
                for key, value in self.doc.axes.items():
                    lines.append(f"{key}: {value}")
            lines.append("")
            lines.append(f"Objects: {len(self.doc.objects)}")
            if self.last_click_world is not None:
                x, y = self._display_point(self.last_click_world)
                lines.append(f"Clicked: ({fmt_num(x)}, {fmt_num(y)})")
            if obj is not None:
                lines.append("")
                lines.extend(obj.summary_lines(transform=self._display_point))
                if self.selected_vertex_index is not None and obj.points:
                    index = min(self.selected_vertex_index, len(obj.points) - 1)
                    vx, vy = self._display_point(obj.points[index])
                    lines.append(f"Nearest vertex: #{index + 1} ({fmt_num(vx)}, {fmt_num(vy)})")
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")
        if obj is None and self.doc is not None:
            self.status_var.set(self._status_text(self.hover_world))
        elif obj is not None:
            self.status_var.set(self._status_text(self.hover_world))


def main() -> None:
    parser = argparse.ArgumentParser(description="Robocon map parameter viewer")
    parser.add_argument("map_path", nargs="?", help="Path to a map JSON file")
    args = parser.parse_args()
    app = MapToolApp(args.map_path)
    app.mainloop()
