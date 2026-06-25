from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .geometry import Point, as_point, bbox, polygon_area, polygon_centroid, polyline_length


@dataclass(slots=True)
class ViewConfig:
    grid_step: float = 500.0
    padding_px: int = 72
    show_grid: bool = True
    show_axes: bool = True
    show_labels: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ViewConfig":
        data = data or {}
        return cls(
            grid_step=float(data.get("grid_step", 500.0)),
            padding_px=int(data.get("padding_px", 72)),
            show_grid=bool(data.get("show_grid", True)),
            show_axes=bool(data.get("show_axes", True)),
            show_labels=bool(data.get("show_labels", True)),
        )


@dataclass(slots=True)
class MapObject:
    id: str
    name: str
    type: str
    points: list[Point] = field(default_factory=list)
    x: float | None = None
    y: float | None = None
    tags: list[str] = field(default_factory=list)
    color: str = "#38bdf8"
    fill: str | None = None
    width: float = 2.0
    radius: float = 6.0
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MapObject":
        raw_type = str(data.get("type", "point")).strip().lower()
        raw_points = data.get("points") or []
        points = [as_point(item) for item in raw_points]
        x = data.get("x")
        y = data.get("y")
        if raw_type == "point":
            if x is None or y is None:
                if points:
                    x, y = points[0]
                else:
                    raise ValueError(f"Point object {data.get('id')!r} is missing coordinates")
            points = [(float(x), float(y))]
        else:
            if not points and x is not None and y is not None:
                points = [(float(x), float(y))]

        style = dict(data.get("style") or {})
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]

        return cls(
            id=str(data.get("id") or data.get("name") or "object"),
            name=str(data.get("name") or data.get("id") or "Object"),
            type=raw_type,
            points=points,
            x=float(x) if x is not None else None,
            y=float(y) if y is not None else None,
            tags=[str(tag) for tag in tags],
            color=str(data.get("color") or style.get("color") or "#38bdf8"),
            fill=(data.get("fill") or style.get("fill")),
            width=float(data.get("width") or style.get("width") or 2.0),
            radius=float(data.get("radius") or style.get("radius") or 6.0),
            note=str(data.get("note") or ""),
            meta={k: v for k, v in data.items() if k not in {
                "id", "name", "type", "points", "x", "y", "tags",
                "color", "fill", "width", "radius", "note", "style",
            }},
        )

    @property
    def is_point(self) -> bool:
        return self.type == "point"

    def geometry_points(self) -> list[Point]:
        if self.is_point:
            return [self.points[0]]
        return list(self.points)

    def bbox(self) -> tuple[float, float, float, float]:
        if self.is_point:
            x, y = self.points[0]
            return x, y, x, y
        return bbox(self.points)

    def center(self) -> Point:
        if self.is_point:
            return self.points[0]
        if self.type == "polygon":
            return polygon_centroid(self.points)
        if self.points:
            xs = [p[0] for p in self.points]
            ys = [p[1] for p in self.points]
            return sum(xs) / len(xs), sum(ys) / len(ys)
        return 0.0, 0.0

    def length(self) -> float:
        return polyline_length(self.points)

    def area(self) -> float:
        return polygon_area(self.points) if self.type == "polygon" else 0.0

    def nearest_vertex(self, point: Point) -> tuple[int, Point, float] | None:
        if not self.points:
            return None
        best_index = 0
        best_point = self.points[0]
        best_dist = (best_point[0] - point[0]) ** 2 + (best_point[1] - point[1]) ** 2
        for index, candidate in enumerate(self.points[1:], start=1):
            dist = (candidate[0] - point[0]) ** 2 + (candidate[1] - point[1]) ** 2
            if dist < best_dist:
                best_index = index
                best_point = candidate
                best_dist = dist
        return best_index, best_point, best_dist ** 0.5

    def summary_lines(self, transform: Callable[[Point], Point] | None = None) -> list[str]:
        transform = transform or (lambda point: point)
        lines = [
            f"ID: {self.id}",
            f"Name: {self.name}",
            f"Type: {self.type}",
        ]
        if self.tags:
            lines.append(f"Tags: {', '.join(self.tags)}")
        if self.is_point:
            x, y = transform(self.points[0])
            lines.append(f"Point: ({x:.3f}, {y:.3f})")
        else:
            lines.append(f"Vertices: {len(self.points)}")
            transformed_points = [transform(point) for point in self.geometry_points()]
            dminx = min(point[0] for point in transformed_points)
            dminy = min(point[1] for point in transformed_points)
            dmaxx = max(point[0] for point in transformed_points)
            dmaxy = max(point[1] for point in transformed_points)
            lines.append(f"BBox: ({dminx:.3f}, {dminy:.3f}) -> ({dmaxx:.3f}, {dmaxy:.3f})")
            if self.type == "line":
                lines.append(f"Length: {self.length():.3f}")
            if self.type == "polygon":
                lines.append(f"Area: {self.area():.3f}")
                cx, cy = transform(self.center())
                lines.append(f"Centroid: ({cx:.3f}, {cy:.3f})")
        if self.note:
            lines.append(f"Note: {self.note}")
        return lines


@dataclass(slots=True)
class MapDocument:
    map_id: str
    name: str
    unit: str
    axes: dict[str, str]
    objects: list[MapObject]
    view: ViewConfig
    explicit_bounds: tuple[float, float, float, float] | None = None
    display_origin: Point = (0.0, 0.0)
    display_axis_x: Point = (1.0, 0.0)
    display_axis_y: Point = (0.0, 1.0)
    display_modes: dict[str, Point] = field(default_factory=lambda: {"default": (0.0, 0.0)})
    default_display_mode: str = "default"
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None = None) -> "MapDocument":
        map_section = dict(data.get("map") or {})
        objects = [MapObject.from_dict(item) for item in data.get("objects", [])]
        bounds_raw = data.get("world_bounds") or data.get("extents") or map_section.get("bounds")
        origin_raw = map_section.get("origin") or data.get("origin")
        axis_basis_raw = map_section.get("axis_basis") or data.get("axis_basis") or {}
        modes_raw = map_section.get("display_modes") or data.get("display_modes") or {}
        default_mode_raw = map_section.get("default_mode") or data.get("default_mode")
        bounds = None
        display_origin = (0.0, 0.0)
        display_axis_x = (1.0, 0.0)
        display_axis_y = (0.0, 1.0)
        display_modes: dict[str, Point] = {}
        default_display_mode = "default"
        if isinstance(bounds_raw, dict):
            bounds = (
                float(bounds_raw.get("min_x", 0.0)),
                float(bounds_raw.get("min_y", 0.0)),
                float(bounds_raw.get("max_x", 0.0)),
                float(bounds_raw.get("max_y", 0.0)),
            )
        elif isinstance(bounds_raw, (list, tuple)) and len(bounds_raw) >= 4:
            bounds = (float(bounds_raw[0]), float(bounds_raw[1]), float(bounds_raw[2]), float(bounds_raw[3]))
        if isinstance(origin_raw, dict):
            display_origin = (
                float(origin_raw.get("x", 0.0)),
                float(origin_raw.get("y", 0.0)),
            )
        elif isinstance(origin_raw, (list, tuple)) and len(origin_raw) >= 2:
            display_origin = (float(origin_raw[0]), float(origin_raw[1]))
        if isinstance(axis_basis_raw, dict):
            x_axis_raw = axis_basis_raw.get("x")
            y_axis_raw = axis_basis_raw.get("y")
            if isinstance(x_axis_raw, (list, tuple)) and len(x_axis_raw) >= 2:
                display_axis_x = (float(x_axis_raw[0]), float(x_axis_raw[1]))
            if isinstance(y_axis_raw, (list, tuple)) and len(y_axis_raw) >= 2:
                display_axis_y = (float(y_axis_raw[0]), float(y_axis_raw[1]))
        if isinstance(modes_raw, dict):
            for name, raw_value in modes_raw.items():
                offset_raw = raw_value.get("origin_offset") if isinstance(raw_value, dict) else raw_value
                if isinstance(offset_raw, dict):
                    display_modes[str(name)] = (
                        float(offset_raw.get("x", 0.0)),
                        float(offset_raw.get("y", 0.0)),
                    )
                elif isinstance(offset_raw, (list, tuple)) and len(offset_raw) >= 2:
                    display_modes[str(name)] = (float(offset_raw[0]), float(offset_raw[1]))
        if not display_modes:
            display_modes = {"default": (0.0, 0.0)}
        if isinstance(default_mode_raw, str) and default_mode_raw in display_modes:
            default_display_mode = default_mode_raw
        elif "mode_red" in display_modes:
            default_display_mode = "mode_red"
        else:
            default_display_mode = next(iter(display_modes))

        return cls(
            map_id=str(map_section.get("id") or data.get("id") or (source_path.stem if source_path else "map")),
            name=str(map_section.get("name") or data.get("name") or "Map"),
            unit=str(map_section.get("unit") or "mm"),
            axes=dict(map_section.get("axes") or {}),
            objects=objects,
            view=ViewConfig.from_dict(data.get("view")),
            explicit_bounds=bounds,
            display_origin=display_origin,
            display_axis_x=display_axis_x,
            display_axis_y=display_axis_y,
            display_modes=display_modes,
            default_display_mode=default_display_mode,
            source_path=source_path,
        )

    @classmethod
    def load(cls, path: str | Path) -> "MapDocument":
        file_path = Path(path).expanduser().resolve()
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data, source_path=file_path)

    def object_by_id(self, object_id: str) -> MapObject | None:
        for obj in self.objects:
            if obj.id == object_id:
                return obj
        return None

    def filtered_objects(self, query: str = "") -> list[MapObject]:
        query = query.strip().lower()
        if not query:
            return list(self.objects)
        result: list[MapObject] = []
        for obj in self.objects:
            haystack = " ".join([obj.id, obj.name, obj.type, " ".join(obj.tags)]).lower()
            if query in haystack:
                result.append(obj)
        return result

    def to_display_point(self, point: Point) -> Point:
        vx = point[0] - self.display_origin[0]
        vy = point[1] - self.display_origin[1]
        axx, axy = self.display_axis_x
        ayx, ayy = self.display_axis_y
        det = axx * ayy - axy * ayx
        if abs(det) < 1e-9:
            return vx, vy
        dx = (vx * ayy - vy * ayx) / det
        dy = (-vx * axy + vy * axx) / det
        return dx, dy

    def from_display_point(self, point: Point) -> Point:
        return (
            self.display_origin[0] + point[0] * self.display_axis_x[0] + point[1] * self.display_axis_y[0],
            self.display_origin[1] + point[0] * self.display_axis_x[1] + point[1] * self.display_axis_y[1],
        )

    def display_mode_names(self) -> list[str]:
        return list(self.display_modes)

    def display_mode_offset(self, mode_name: str | None = None) -> Point:
        mode_name = mode_name or self.default_display_mode
        return self.display_modes.get(mode_name, (0.0, 0.0))

    def to_mode_display_point(self, point: Point, mode_name: str | None = None) -> Point:
        dx, dy = self.to_display_point(point)
        ox, oy = self.display_mode_offset(mode_name)
        return dx - ox, dy - oy

    def mode_origin_world(self, mode_name: str | None = None) -> Point:
        ox, oy = self.display_mode_offset(mode_name)
        return self.from_display_point((ox, oy))

    def bounds(self) -> tuple[float, float, float, float]:
        if self.explicit_bounds is not None:
            min_x, min_y, max_x, max_y = self.explicit_bounds
            return min(min_x, max_x), min(min_y, max_y), max(min_x, max_x), max(min_y, max_y)
        points: list[Point] = []
        for obj in self.objects:
            points.extend(obj.geometry_points())
        if not points:
            return 0.0, 0.0, 1000.0, 1000.0
        min_x = min(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_x = max(p[0] for p in points)
        max_y = max(p[1] for p in points)
        return min_x, min_y, max_x, max_y
