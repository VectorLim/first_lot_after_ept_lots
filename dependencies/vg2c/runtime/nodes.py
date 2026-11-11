"""Resolve (site, database) pairs to the fixed backend node strings."""

from __future__ import annotations

MARS_NODES = {
    "KM": "KM.[A15_PROD_21.].MARS",
    "CD": "CD.[A48_PROD_21.].MARS",
    "PG": "PG.[A12_PROD_0.].MARS",
    "KM8": "KM8.[A88_PROD_21.].MARS",
    "VN": "VN.[A90_PROD_21.].MARS",
}
SITES = frozenset(MARS_NODES)
DATABASES = frozenset({"MARS", "ARIES", "OASYS"})
FACILITIES = {"KM": "A15", "CD": "A48", "PG": "A12", "KM8": "A88", "VN": "A90"}


def normalize_site(site: str) -> str:
    key = site.strip().upper()
    if key not in SITES:
        raise ValueError(f"Unknown site {site!r}; expected one of {sorted(SITES)}")
    return key


def site_facility(site: str) -> str:
    """Return the facility code for one site, e.g. "KM" -> "A15"."""
    return FACILITIES[normalize_site(site)]


def site_node(database: str, site: str) -> str:
    """Return the node for one database at one site, e.g. ("ARIES", "KM") -> "KM.ARIES"."""
    database_key = database.strip().upper()
    if database_key not in DATABASES:
        raise ValueError(
            f"Unknown database {database!r}; expected one of {sorted(DATABASES)}"
        )
    site_key = normalize_site(site)
    if database_key == "MARS":
        return MARS_NODES[site_key]
    return f"{site_key}.{database_key}"
