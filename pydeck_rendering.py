from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

import math
import os

import pandas as pd
import pydeck as pdk

@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    name: str


LocationLike = Union[Location, Tuple[float, float, str]]


def _to_locations(locations: Sequence[LocationLike]) -> List[Location]:
    out: List[Location] = []
    for item in locations:
        if isinstance(item, Location):
            out.append(item)
        else:
            lat, lon, name = item
            out.append(Location(float(lat), float(lon), str(name)))
    return out


def _auto_zoom(project_lat: float, project_lon: float, locs: Sequence[Location]) -> int:
    def hav_km(lat1, lon1, lat2, lon2):
        r = 6371.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    if not locs:
        return 12

    max_km = max(hav_km(project_lat, project_lon, l.lat, l.lon) for l in locs)

    if max_km < 25:
        return 12
    if max_km < 100:
        return 10
    if max_km < 500:
        return 7
    if max_km < 1500:
        return 5
    return 3


def _pick_basemap(basemap_provider: str, mapbox_style: str) -> str:
    basemap_provider = basemap_provider.lower().strip()
    if basemap_provider == "mapbox":
        # Use Mapbox styles; requires mapbox api key
        return mapbox_style
    if basemap_provider == "carto":
        # Public basemap (no token)
        # Other options:
        #  - "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
        #  - "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json"
        return "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
    raise ValueError("basemap_provider must be 'mapbox' or 'carto'")


def make_project_arc_deck(
    project_lat: float,
    project_lon: float,
    locations: Sequence[LocationLike],
    *,
    output_html: Optional[str] = None,
    return_html_string: bool = True,
    show_labels: bool = False,
    basemap_provider: str = "carto",
    mapbox_style: str | None = None,
    mapbox_api_key: str | None = None,
    text_size: int = 12,
    label_offset_deg: float = 0.12,
    initial_zoom: Optional[int] = None,
) -> pdk.Deck:
    """
    Creates a pydeck.Deck with arcs over a real basemap.

    For basemap_provider="mapbox":
      - pass mapbox_api_key parameter

    For basemap_provider="carto":
      - no token needed
    """
    locs = _to_locations(locations)

    arc_df = pd.DataFrame(
        [
            {
                "name": l.name,
                "src_lon": l.lon,
                "src_lat": l.lat,
                "tgt_lon": float(project_lon),
                "tgt_lat": float(project_lat),
            }
            for l in locs
        ]
    )

    label_df = pd.DataFrame(
        [
            {
                "text": l.name,
                "lon": l.lon + label_offset_deg,
                "lat": l.lat,
            }
            for l in locs
        ]
    )

    points_df = pd.DataFrame(
        [{"type": "project", "name": "Project", "lon": project_lon, "lat": project_lat}]
        + [{"type": "location", "name": l.name, "lon": l.lon, "lat": l.lat} for l in locs]
    )

    arc_layer = pdk.Layer(
        "ArcLayer",
        arc_df,
        get_source_position=["src_lon", "src_lat"],
        get_target_position=["tgt_lon", "tgt_lat"],
        get_width=2,
        get_height = .3,
        pickable=True,
        auto_highlight=True,
        get_source_color=[0, 165, 180, 170],  # teal
        get_target_color=[255, 120, 90, 190],  # coral
    )

    arc_glow_layer = pdk.Layer(
        "ArcLayer",
        arc_df,
        get_source_position=["src_lon", "src_lat"],
        get_target_position=["tgt_lon", "tgt_lat"],
        get_width=5,
        get_height=0.3,
        get_source_color=[0, 165, 180, 60],
        get_target_color=[255, 120, 90, 60],
        pickable=False,
    )

    scatter_layer = pdk.Layer(
        "ScatterplotLayer",
        points_df,
        get_position=["lon", "lat"],
        get_radius=10000,
        radius_min_pixels=2,
        radius_max_pixels=6,
        get_fill_color="type == 'project' ? [0, 200, 0, 220] : [0, 0, 0, 180]",
        pickable=True,
    )

    layers = [arc_layer, arc_glow_layer, scatter_layer]
    if show_labels:
        text_layer = pdk.Layer(
            "TextLayer",
            label_df,
            get_position=["lon", "lat"],
            get_text="text",
            get_size=f"zoom < 16 ? 0 : {text_size}",
            get_angle=0,
            get_color=[20, 20, 20, 230],
            get_text_anchor='"start"',
            get_alignment_baseline='"center"',
            pickable=False,
        )
        layers.append(text_layer)

    tooltip = {
        "html": (
            "<b>{name}</b><br/>"
            "Source: ({src_lat}, {src_lon})<br/>"
            "Project: ({tgt_lat}, {tgt_lon})"
        ),
        "style": {"backgroundColor": "white", "color": "black"},
    }

    if initial_zoom is None:
        initial_zoom = _auto_zoom(project_lat, project_lon, locs)

    view_state = pdk.ViewState(
        latitude=float(project_lat),
        longitude=float(project_lon),
        zoom=int(initial_zoom),
        pitch=35,
        bearing=0,
    )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style=_pick_basemap(basemap_provider, mapbox_style),
        tooltip=tooltip,
    )

    # Ensure Mapbox token is wired when using Mapbox styles
    if basemap_provider.lower().strip() == "mapbox":
        if not mapbox_api_key:
            raise RuntimeError(
                "Mapbox basemap selected but no token found. "
                "Pass mapbox_api_key parameter  "
                "or use basemap_provider='carto'."
            )
        pdk.settings.mapbox_api_key = mapbox_api_key

    if output_html:
        deck.to_html(output_html, open_browser=False)
    elif return_html_string:
        return deck.to_html(as_string=True)

    return deck