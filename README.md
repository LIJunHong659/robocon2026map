# Map Parameter Tool

Zero-dependency Tkinter tool for quickly checking Robocon map points.

## Run

```bash
uv run python main.py
```

or

```bash
uv run map-tool
```

## Features

- Load a map JSON file
- Click objects or empty space to get coordinates
- Zoom, pan, fit to view
- Copy object, vertex, or clicked point as JSON

## Data format

Edit `maps/robocon_2026_template.json` and replace the demo coordinates with your real field data.
