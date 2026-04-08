import html
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import folium
import pandas as pd
import requests
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

from popup_utils import build_popup_html

st.set_page_config(page_title="Melbourne Support Finder", layout="wide")

APP_DIR = Path(__file__).resolve().parent
GOV_SUPPORT_CARDS_CSS_PATH = APP_DIR / "static" / "gov_support_cards.css"
RESULTS_CARDS_CSS_PATH = APP_DIR / "static" / "results_cards.css"

# ---------- Constants ----------
MELB_COORDS = "(-38.40,144.60,-37.45,145.50)"
# Melbourne CBD (Hoddle Grid) — fixed centre when the map first loads
MELBOURNE_CBD_LAT = -37.8136
MELBOURNE_CBD_LON = 144.9631
MAP_DEFAULT_ZOOM = 13
DB_PATH = "community_food_support.db"

OVERPASS_URLS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

PUBLIC_TOILETS_URL = "https://data.melbourne.vic.gov.au/api/v2/catalog/datasets/public-toilets/exports/json"
HELPING_OUT_URL = (
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "free-and-cheap-support-services-with-opening-hours-public-transport-and-parking-/records"
)

OSM_QUERY = f"""
[out:json][timeout:25];
(
  node["amenity"="social_facility"]["social_facility"="food_bank"]{MELB_COORDS};
  way["amenity"="social_facility"]["social_facility"="food_bank"]{MELB_COORDS};

  node["amenity"="social_facility"]["social_facility"="soup_kitchen"]{MELB_COORDS};
  way["amenity"="social_facility"]["social_facility"="soup_kitchen"]{MELB_COORDS};

  node["amenity"="food_sharing"]{MELB_COORDS};
  way["amenity"="food_sharing"]{MELB_COORDS};

  node["amenity"="social_facility"]["social_facility"="shelter"]{MELB_COORDS};
  way["amenity"="social_facility"]["social_facility"="shelter"]{MELB_COORDS};

  node["amenity"="social_facility"]["social_facility"="group_home"]{MELB_COORDS};
  way["amenity"="social_facility"]["social_facility"="group_home"]{MELB_COORDS};
);
out center;
"""

TYPE_ORDER = [
    "Food",
    "Shelter",
    "Youth Shelter",
    "Support Services",
    "Charity Organisation",
    "Religious / Community Support",
    "Sanitation",
]

TYPE_TO_ICON = {
    "Food": ("green", "cutlery"),
    "Shelter": ("red", "home"),
    "Youth Shelter": ("cadetblue", "home"),
    "Support Services": ("darkblue", "plus"),
    "Charity Organisation": ("blue", "info-sign"),
    "Religious / Community Support": ("purple", "plus"),
    "Sanitation": ("orange", "tint"),
}

HELPING_OUT_TEXT_COLS = [
    "name", "what", "who", "category_1", "category_2",
    "category_3", "category_4", "category_5", "category_6"
]

FOOD_KEYWORDS = [
    "food", "meal", "meals", "pantry", "soup", "kitchen", "relief",
    "groceries", "parcel", "breakfast", "lunch", "dinner",
    "fareshare", "secondbite", "ozharvest", "community meal",
    "food parcel", "Food", "voucher"
]

SUPPORT_KEYWORDS = [
    "community", "care", "mission", "relief", "outreach", "parish",
    "salvation army", "st vincent de paul", "vinnies", "wesley",
    "anglicare", "unitingcare", "baptcare"
]

DV_KEYWORDS = [
    "domestic violence", "family violence", "women's refuge",
    "womens refuge", "safe steps", "violence support"
]

DRUG_ALCOHOL_KEYWORDS = [
    "drug", "alcohol", "aod", "addiction", "detox",
    "rehab", "rehabilitation", "substance"
]

AGED_CARE_KEYWORDS = [
    "aged care", "aged-care", "elderly", "seniors", "senior",
    "senior citizens", "retirement", "retirement living",
    "nursing home", "residential care", "care residence",
    "care home", "aged services", "home care package", "home care packages"
]

SHELTER_KEYWORDS = [
    "accommodation", "crisis accommodation", "homeless", "homelessness",
    "housing", "rough sleeping", "sleeping rough", "supported housing",
    "transitional housing", "night shelter", "rooming",
    "common ground", "launch housing", "house of welcome", "salvation army"
]

HELPING_OUT_SUPPORT_KEYWORDS = [
    "drug", "alcohol", "aod", "addiction", "detox", "rehab",
    "rehabilitation", "substance", "family violence",
    "domestic violence", "women's support", "womens support",
    "counselling", "counseling", "mental health", "wellbeing",
    "support", "social work", "needle and syringe", "crisis"
]

HELPING_OUT_SUPPORT_EXCLUDE = [
    "Food", "food parcel", "meal", "meals", "soup kitchen",
    "accommodation", "housing", "homeless", "homelessness",
    "rough sleeping", "sleeping rough"
]

HYGIENE_KEYWORDS = [
    "shower", "showers", "laundry", "washing", "washing machine",
    "washer", "dryer", "clothes washing", "toiletries", "hygiene"
]

GOV_SUPPORT_CARDS = [
    {
        "emoji": "🛏️",
        "title": "Accommodation",
        "caption": "Homelessness / urgent accommodation",
        "button": "Accommodation help",
        "url": "https://services.dffh.vic.gov.au/getting-help",
    },
    {
        "emoji": "🛡️",
        "title": "Family Violence",
        "caption": "Family violence support",
        "button": "Family Violence Support",
        "url": "https://www.vic.gov.au/family-violence-statewide-support-services",
    },
    {
        "emoji": "🚨",
        "title": "Emergency - 000",
        "caption": "Immediate danger or emergency",
        "button": "Emergency help",
        "url": "https://www.triplezero.vic.gov.au/",
    },
    {
        "emoji": "💊",
        "title": "Drugs & Alcohol",
        "caption": "For drug and alcohol support",
        "button": "Drug & alcohol help",
        "url": "https://www.health.vic.gov.au/aod-treatment-services/telephone-and-online-services",
    },
    {
        "emoji": "🥫",
        "title": "Food",
        "caption": "Food relief and support services",
        "button": "Food relief help",
        "url": "https://providers.dffh.vic.gov.au/community-food-relief",
    },
]

# ---------- DB ----------
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS food_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            notes TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ---------- Helpers ----------
def rate_limit():
    if "last_request" not in st.session_state:
        st.session_state.last_request = 0
    now = time.time()
    if now - st.session_state.last_request < 2:
        st.warning("Please wait before making another request")
        st.stop()
    st.session_state.last_request = now

def geocode_address(address: str):
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{address}, Melbourne, Victoria, Australia",
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "au",
            },
            timeout=20,
            headers={"User-Agent": "Melbourne Support Finder"},
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None, None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except requests.exceptions.RequestException:
        return None, None


def address_from_tags(tags):
    parts = [tags.get(k, "") for k in ["addr:housenumber", "addr:street", "addr:suburb", "addr:postcode"]]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else "No address listed"


def has_any_keyword(text: str, keywords: list[str]) -> bool:
    return any(k in text for k in keywords)


def classify_osm(tags):
    social = tags.get("social_facility", "")
    office = tags.get("office", "")
    amenity = tags.get("amenity", "")
    social_for = str(tags.get("social_facility:for", "")).lower()

    text = " ".join([
        str(tags.get("name", "")),
        str(tags.get("description", "")),
        str(tags.get("operator", "")),
        str(tags.get("website", "")),
        str(tags.get("denomination", "")),
    ]).lower()

    if social in {"food_bank", "soup_kitchen"} or amenity == "food_sharing":
        return "Food"

    if social in {"shelter", "group_home"}:
        if has_any_keyword(text, AGED_CARE_KEYWORDS):
            return "Unknown"
        if "juvenile" in social_for or "youth" in text:
            return "Youth Shelter"
        if "woman" in social_for or has_any_keyword(text, DV_KEYWORDS):
            return "Women's Shelter"
        return "Shelter"

    if office == "charity":
        if has_any_keyword(text, FOOD_KEYWORDS):
            return "Food"
        if has_any_keyword(text, DRUG_ALCOHOL_KEYWORDS):
            return "Support Services"
        if has_any_keyword(text, DV_KEYWORDS):
            return "Women's Shelter"
        return "Charity Organisation"

    if amenity == "community_centre":
        if has_any_keyword(text, FOOD_KEYWORDS):
            return "Food"
        if has_any_keyword(text, DRUG_ALCOHOL_KEYWORDS):
            return "Support Services"
        if has_any_keyword(text, DV_KEYWORDS):
            return "Women's Shelter"
        return "Unknown"

    if amenity == "place_of_worship":
        if has_any_keyword(text, FOOD_KEYWORDS):
            return "Food"
        if has_any_keyword(text, DRUG_ALCOHOL_KEYWORDS):
            return "Support Services"
        if has_any_keyword(text, DV_KEYWORDS):
            return "Women's Shelter"
        if has_any_keyword(text, SUPPORT_KEYWORDS):
            return "Religious / Community Support"
        return "Unknown"

    return "Unknown"


def is_fully_unknown(row):
    return (
        row["name"] == "Unknown"
        and row["type"] == "Unknown"
        and row["address"] == "No address listed"
        and row["phone"] == "No phone listed"
        and row["website"] == "No website listed"
    )


def marker_style(service_type):
    return TYPE_TO_ICON.get(service_type, ("gray", "info-sign"))


def marker_style_for_row(row):
    if row.get("source") == "Community food offer":
        return ("darkgreen", "star")
    if row.get("type") == "Sanitation":
        if row.get("source") == "City of Melbourne Public Toilets":
            return ("orange", "info-sign")
        return ("blue", "tint")
    return marker_style(row["type"])


def dedupe_locations(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["name_key"] = df["name"].fillna("").str.strip().str.lower()
    df["lat_round"] = df["lat"].round(4)
    df["lon_round"] = df["lon"].round(4)
    return (
        df.drop_duplicates(subset=["name_key", "lat_round", "lon_round"])
        .drop(columns=["name_key", "lat_round", "lon_round"])
        .reset_index(drop=True)
    )


def apply_detail_filters(df: pd.DataFrame, show_only_phone: bool, show_only_website: bool, show_only_address: bool) -> pd.DataFrame:
    if show_only_phone:
        df = df[df["phone"] != "No phone listed"]
    if show_only_website:
        df = df[df["website"] != "No website listed"]
    if show_only_address:
        df = df[df["address"] != "No address listed"]
    return df.reset_index(drop=True)


def apply_search_filter(df: pd.DataFrame, search_term: str) -> pd.DataFrame:
    if not search_term:
        return df

    q = search_term.strip().lower()
    search_cols = ["name", "address", "phone", "website", "notes", "source"]
    mask = False

    for col in search_cols:
        if col in df.columns:
            mask = mask | df[col].fillna("").astype(str).str.lower().str.contains(q, na=False, regex=False)

    return df[mask].reset_index(drop=True)


def normalise_helping_out_df(df: pd.DataFrame, out_type: str, notes: str) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    df["name"] = df["name"].fillna("Unknown") if "name" in df.columns else "Unknown"

    address_cols = [c for c in ["address_1", "address_2", "suburb"] if c in df.columns]
    if address_cols:
        df["address"] = (
            df[address_cols]
            .fillna("")
            .agg(", ".join, axis=1)
            .str.replace(r"(,\s*)+", ", ", regex=True)
            .str.strip(", ")
        ).replace("", "No address listed")
    else:
        df["address"] = "No address listed"

    df["phone"] = df["phone"].fillna("No phone listed") if "phone" in df.columns else "No phone listed"
    df["website"] = df["website"].fillna("No website listed") if "website" in df.columns else "No website listed"
    df["hours"] = df["opening_hours"].fillna("") if "opening_hours" in df.columns else ""

    df["lat"] = pd.to_numeric(df["latitude"], errors="coerce") if "latitude" in df.columns else pd.NA
    df["lon"] = pd.to_numeric(df["longitude"], errors="coerce") if "longitude" in df.columns else pd.NA
    df = df.dropna(subset=["lat", "lon"]).copy()

    df["type"] = out_type
    df["source"] = "City of Melbourne Helping Out"
    df["notes"] = notes

    keep_cols = ["name", "type", "lat", "lon", "address", "phone", "website", "hours", "source", "notes"]
    return df[keep_cols].drop_duplicates().reset_index(drop=True)

# ---------- Loaders ----------
@st.cache_data(ttl=30)
def load_custom_food_offers():
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM food_offers", conn)
    conn.close()

    if df.empty:
        return df

    df["type"] = "Food"
    df["hours"] = ""
    df["source"] = "Community food offer"
    df["notes"] = df["notes"].fillna("Food support submitted through the app.")
    df["address"] = df["address"].fillna("No address listed")
    df["phone"] = df["phone"].fillna("No phone listed")
    df["website"] = df["website"].fillna("No website listed")

    keep_cols = ["name", "type", "lat", "lon", "address", "phone", "website", "hours", "source", "notes"]
    return df[keep_cols].dropna(subset=["lat", "lon"]).drop_duplicates().reset_index(drop=True)


@st.cache_data(ttl=86400)
def load_osm_data():
    data = None
    errors = []

    for url in OVERPASS_URLS:
        try:
            response = requests.get(
                url,
                params={"data": OSM_QUERY},
                timeout=60,
                headers={"User-Agent": "Streamlit Melbourne Support Finder"},
            )
            response.raise_for_status()
            data = response.json()
            break
        except requests.exceptions.RequestException as e:
            errors.append(f"{url} -> {e}")

    if data is None:
        return pd.DataFrame(columns=[
            "name", "type", "lat", "lon", "address",
            "phone", "website", "hours", "source", "notes"
        ])

    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")

        if lat is None or lon is None:
            continue

        row = {
            "name": tags.get("name", "Unknown"),
            "type": classify_osm(tags),
            "lat": lat,
            "lon": lon,
            "address": address_from_tags(tags),
            "phone": tags.get("phone", "No phone listed"),
            "website": tags.get("website", "No website listed"),
            "hours": "",
            "source": "OSM",
            "notes": "",
        }

        if row["type"] == "Religious / Community Support":
            has_name = row["name"] != "Unknown"
            has_address = row["address"] != "No address listed"
            has_phone = row["phone"] != "No phone listed"
            if not has_name or not (has_address or has_phone):
                continue
            row["notes"] = "Religious or community-linked venue. Support availability is not guaranteed; contact directly where possible."

        rows.append(row)

    df = pd.DataFrame(rows).drop_duplicates()
    if df.empty:
        return df

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    df = df[~df.apply(is_fully_unknown, axis=1)].reset_index(drop=True)
    return df


@st.cache_data(ttl=86400)
def fetch_helping_out_raw():
    all_rows = []
    offset = 0
    limit = 100

    while True:
        response = requests.get(
            HELPING_OUT_URL,
            params={"limit": limit, "offset": offset},
            timeout=60,
            headers={"User-Agent": "Streamlit Melbourne Support Finder"},
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])

        if not results:
            break

        all_rows.extend(results)

        if len(results) < limit:
            break

        offset += limit

    df = pd.DataFrame(all_rows)
    if df.empty:
        return df

    text_cols = [c for c in HELPING_OUT_TEXT_COLS if c in df.columns]
    if not text_cols:
        return pd.DataFrame()

    df["search_text"] = df[text_cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    return df


@st.cache_data(ttl=86400)
def load_helping_out_food_data():
    df = fetch_helping_out_raw()
    if df.empty:
        return df

    df = df[df["search_text"].apply(lambda x: has_any_keyword(x, FOOD_KEYWORDS))].copy()
    return normalise_helping_out_df(df, "Food", "Food-related support service from City of Melbourne Helping Out.")


@st.cache_data(ttl=86400)
def load_helping_out_shelter_data():
    df = fetch_helping_out_raw()
    if df.empty:
        return df

    df = df[
        df["search_text"].apply(lambda x: has_any_keyword(x, SHELTER_KEYWORDS))
        & ~df["search_text"].apply(lambda x: has_any_keyword(x, AGED_CARE_KEYWORDS))
    ].copy()

    return normalise_helping_out_df(df, "Shelter", "Accommodation or homelessness-related support service from City of Melbourne Helping Out.")


@st.cache_data(ttl=86400)
def load_helping_out_support_data():
    df = fetch_helping_out_raw()
    if df.empty:
        return df

    include_mask = df["search_text"].apply(lambda x: has_any_keyword(x, HELPING_OUT_SUPPORT_KEYWORDS))
    exclude_mask = df["search_text"].apply(lambda x: has_any_keyword(x, HELPING_OUT_SUPPORT_EXCLUDE))
    df = df[include_mask & ~exclude_mask].copy()

    return normalise_helping_out_df(df, "Support Services", "Drug, alcohol, family violence or general support service from City of Melbourne Helping Out.")

@st.cache_data(ttl=86400)
def load_helping_out_hygiene_data():
    df = fetch_helping_out_raw()
    if df.empty:
        return df

    df = df[df["search_text"].apply(lambda x: has_any_keyword(x, HYGIENE_KEYWORDS))].copy()

    return normalise_helping_out_df(
        df,
        "Sanitation",
        "Shower, laundry or hygiene-related service from City of Melbourne Helping Out."
    )

@st.cache_data(ttl=86400)
def load_sanitation_data():
    try:
        response = requests.get(
            PUBLIC_TOILETS_URL,
            timeout=60,
            headers={"User-Agent": "Streamlit Melbourne Support Finder"},
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        return pd.DataFrame(columns=[
            "name", "type", "lat", "lon", "address",
            "phone", "website", "hours", "source", "notes"
        ])

    df = pd.DataFrame(data)
    if df.empty:
        return df

    lat_col = next((c for c in ["latitude", "Latitude", "lat"] if c in df.columns), None)
    lon_col = next((c for c in ["longitude", "Longitude", "lon", "lng"] if c in df.columns), None)

    if lat_col is None or lon_col is None:
        return pd.DataFrame(columns=[
            "name", "type", "lat", "lon", "address",
            "phone", "website", "hours", "source", "notes"
        ])

    df["lat"] = pd.to_numeric(df[lat_col], errors="coerce")
    df["lon"] = pd.to_numeric(df[lon_col], errors="coerce")
    df["name"] = df["name"].fillna("Public Toilet") if "name" in df.columns else "Public Toilet"
    df["address"] = df["address"].fillna("No address listed") if "address" in df.columns else "No address listed"
    df["type"] = "Sanitation"
    df["phone"] = "No phone listed"
    df["website"] = "No website listed"
    df["hours"] = ""
    df["public_transport"] = ""
    df["source"] = "City of Melbourne Public Toilets"
    df["notes"] = "Public toilet location."

    keep_cols = ["name", "type", "lat", "lon", "address", "phone", "website", "hours", "source", "notes"]
    return df[keep_cols].dropna(subset=["lat", "lon"]).drop_duplicates().reset_index(drop=True)


# ---------- UI ----------
def render_header():
    st.markdown(
        """
        <h1 style="text-align: center; font-size: 3.2rem; margin-bottom: 0.2rem;">
            Melbourne Support Finder
        </h1>
        """,
        unsafe_allow_html=True
    )
    st.markdown("## Victorian Government support services")

    card_blocks = []
    for card in GOV_SUPPORT_CARDS:
        title = html.escape(card["title"])
        caption = html.escape(card["caption"])
        button = html.escape(card["button"])
        url = html.escape(card["url"], quote=True)
        emoji = card["emoji"]
        # No leading indentation on HTML lines — Streamlit markdown treats 4+ space indents as code fences.
        card_blocks.append(
            f'<div class="gov-support-card">'
            f'<h3 class="gov-support-card-title">{emoji} {title}</h3>'
            f'<p class="gov-support-card-caption">{caption}</p>'
            f'<a class="gov-support-card-link" href="{url}" target="_blank" rel="noopener noreferrer">{button}</a>'
            f"</div>"
        )
    cards_inner = "\n".join(card_blocks)
    gov_support_css = GOV_SUPPORT_CARDS_CSS_PATH.read_text(encoding="utf-8")
    st.markdown(
        "<style>\n"
        + gov_support_css
        + "\n</style>\n"
        '<div class="gov-support-grid">\n'
        + cards_inner
        + "\n</div>",
        unsafe_allow_html=True,
    )


@st.dialog("Offer food support")
def food_offer_dialog():
    with st.form("food_offer_form"):
        st.write("Add a restaurant, uni café, or other place offering food support.")
        name = st.text_input("Organisation / venue name*")
        address = st.text_input("Address*")
        phone = st.text_input("Phone")
        website = st.text_input("Website")
        notes = st.text_area("Notes", placeholder="e.g. free meals after 5pm on weekdays")

        if st.form_submit_button("Submit"):
            rate_limit()
            if not name.strip():
                st.warning("Name is required.")
                return
            if not address.strip():
                st.warning("Address is required.")
                return

            lat, lon = geocode_address(address.strip())
            if lat is None or lon is None:
                st.warning("Could not find that address on the map. Please check the address and try again.")
                return

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO food_offers (name, address, phone, website, notes, lat, lon, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name.strip(),
                address.strip(),
                phone.strip(),
                website.strip(),
                notes.strip(),
                lat,
                lon,
                datetime.utcnow().isoformat()
            ))
            conn.commit()
            conn.close()

            st.cache_data.clear()
            st.session_state["selected_type"] = "Food"
            st.success("Food provider added.")
            st.rerun()


def render_quick_actions():
    st.subheader("Quick Actions")
    qa1, qa2, qa3, qa4 = st.columns(4)

    if qa1.button("Need food", use_container_width=True):
        st.session_state["selected_type"] = "Food"
    if qa2.button("Need shelter", use_container_width=True):
        st.session_state["selected_type"] = "Shelter"
    if qa3.button("Need Sanitation", use_container_width=True):
        st.session_state["selected_type"] = "Sanitation"
    if qa4.button("Need support", use_container_width=True):
        st.session_state["selected_type"] = "Support Services"


def build_available_filters(
    osm_df,
    helping_out_food_df,
    custom_food_df,
    sanitation_df,
    helping_out_shelter_df,
    helping_out_support_df,
    helping_out_hygiene_df,
):
    available = []
    osm_types = set(osm_df["type"].dropna().unique().tolist()) if not osm_df.empty else set()

    for f in TYPE_ORDER:
        if f == "Food":
            if "Food" in osm_types or not helping_out_food_df.empty or not custom_food_df.empty:
                available.append(f)
        elif f == "Shelter":
            if "Shelter" in osm_types or not helping_out_shelter_df.empty:
                available.append(f)
        elif f == "Support Services":
            support_osm_types = {"Charity Organisation", "Religious / Community Support", "Women's Shelter"}
            if any(t in osm_types for t in support_osm_types) or not helping_out_support_df.empty:
                available.append(f)
        elif f == "Sanitation":
            if not sanitation_df.empty or not helping_out_hygiene_df.empty:
                available.append(f)
        else:
            if f in osm_types:
                available.append(f)

    return available


def render_sidebar(available_filters):
    with st.sidebar:
        st.header("Filters")

        default_type = st.session_state.get("selected_type", available_filters[0])
        if default_type not in available_filters:
            default_type = available_filters[0]

        selected_type = st.selectbox(
            "Filter by service type",
            available_filters,
            index=available_filters.index(default_type),
        )

        search_term = st.text_input(
            "Search within current filter",
            placeholder="e.g. Launch Housing, Salvation Army, Southbank"
        )

        show_only_phone = st.checkbox("Only show places with phone", value=False)
        show_only_website = st.checkbox("Only show places with website", value=False)
        show_only_address = st.checkbox("Only show places with address", value=False)

        st.divider()
        st.caption("Marker colours")
        for t in available_filters:
            if t in TYPE_TO_ICON:
                color, _ = marker_style(t)
                st.markdown(f"- **{t}**: {color}")

        st.divider()
        st.subheader("Offer food support")
        st.caption("Restaurants, cafés or organisations can add a food support location.")
        if st.button("Add food provider", use_container_width=True):
            food_offer_dialog()

    return selected_type, search_term, show_only_phone, show_only_website, show_only_address


def build_filtered_df(
    selected_type,
    osm_df,
    helping_out_food_df,
    custom_food_df,
    sanitation_df,
    helping_out_shelter_df,
    helping_out_support_df,
    helping_out_hygiene_df,
):
    if selected_type == "Sanitation":
        return pd.concat([sanitation_df, helping_out_hygiene_df], ignore_index=True)

    if selected_type == "Food":
        osm_food_df = osm_df[osm_df["type"] == "Food"].copy() if "type" in osm_df.columns else pd.DataFrame()
        return pd.concat([osm_food_df, helping_out_food_df, custom_food_df], ignore_index=True)

    if selected_type == "Shelter":
        osm_shelter_df = osm_df[osm_df["type"] == "Shelter"].copy() if "type" in osm_df.columns else pd.DataFrame()
        if not osm_shelter_df.empty:
            osm_shelter_df = osm_shelter_df[
                ~osm_shelter_df["name"].fillna("").str.lower().apply(lambda x: has_any_keyword(x, AGED_CARE_KEYWORDS))
            ].copy()
        return pd.concat([osm_shelter_df, helping_out_shelter_df], ignore_index=True)

    if selected_type == "Support Services":
        if "type" in osm_df.columns:
            osm_support_df = osm_df[
                osm_df["type"].isin(["Charity Organisation", "Religious / Community Support", "Women's Shelter"])
            ].copy()
        else:
            osm_support_df = pd.DataFrame()
        return pd.concat([osm_support_df, helping_out_support_df], ignore_index=True)

    return osm_df[osm_df["type"] == selected_type].reset_index(drop=True) if "type" in osm_df.columns else pd.DataFrame()


def render_metrics(df):
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Locations found", len(df))
    m2.metric("With phone", int((df["phone"] != "No phone listed").sum()) if not df.empty else 0)
    m3.metric("With website", int((df["website"] != "No website listed").sum()) if not df.empty else 0)
    m4.metric("With address", int((df["address"] != "No address listed").sum()) if not df.empty else 0)


def render_map(df):
    m = folium.Map(
        location=[MELBOURNE_CBD_LAT, MELBOURNE_CBD_LON],
        zoom_start=MAP_DEFAULT_ZOOM,
    )
    cluster = MarkerCluster().add_to(m)

    for _, row in df.iterrows():
        color, icon_name = marker_style_for_row(row)
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=folium.Popup(build_popup_html(row), max_width=320),
            tooltip=row["name"],
            icon=folium.Icon(color=color, icon=icon_name),
        ).add_to(cluster)

    st_folium(m, width=None, height=720)


def _result_text_for_html(val) -> str:
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except TypeError:
        pass
    return html.escape(str(val))


def _result_website_html(website) -> str:
    w = "" if website is None else str(website).strip()
    if not w or w == "No website listed":
        return _result_text_for_html(w or "No website listed")
    low = w.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return (
            f'<a href="{html.escape(w, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{html.escape(w)}</a>"
        )
    return html.escape(w)


def _result_card_html(row) -> str:
    name = _result_text_for_html(row["name"])
    rtype = _result_text_for_html(row["type"])
    source = row.get("source", "")

    if source == "City of Melbourne Public Toilets":
        return (
            '<div class="result-card">'
            f'<h3 class="result-card-title">{name}</h3>'
            f'<p class="result-card-type">{rtype}</p>'
            "</div>"
        )

    address = _result_text_for_html(row["address"])
    phone = _result_text_for_html(row["phone"])
    website_inner = _result_website_html(row.get("website"))

    meta = ""
    if row.get("source") == "Community food offer":
        meta = '<p class="result-card-meta">Submitted via community form</p>'

    notes = row.get("notes", "")
    notes_block = ""
    if notes is not None:
        try:
            if isinstance(notes, float) and pd.isna(notes):
                notes = None
        except TypeError:
            pass
    if notes is not None and str(notes).strip():
        notes_block = f'<p class="result-card-notes">{html.escape(str(notes).strip())}</p>'

    return (
        '<div class="result-card">'
        f'<h3 class="result-card-title">{name}</h3>'
        f'<p class="result-card-type">{rtype}</p>'
        f"{meta}"
        '<p class="result-card-field"><span class="result-card-label">Address:</span> '
        f"{address}</p>"
        '<p class="result-card-field"><span class="result-card-label">Phone:</span> '
        f"{phone}</p>"
        '<p class="result-card-field"><span class="result-card-label">Website:</span> '
        f"{website_inner}</p>"
        f"{notes_block}"
        "</div>"
    )


def render_results(df, selected_type):
    st.subheader(f"Results – {selected_type}")

    cards_html = "".join(_result_card_html(row) for _, row in df.iterrows())
    results_css = RESULTS_CARDS_CSS_PATH.read_text(encoding="utf-8")
    st.markdown(
        "<style>\n"
        + results_css
        + "\n</style>\n"
        '<div class="results-grid">'
        + cards_html
        + "</div>",
        unsafe_allow_html=True,
    )


def render_raw_table(df):
    with st.expander("Show raw table"):
        st.dataframe(
            df[["name", "type", "address", "phone", "website"]],
            width="stretch",
            hide_index=True,
        )


# ---------- App ----------
render_header()

osm_df = load_osm_data()
helping_out_food_df = load_helping_out_food_data()
custom_food_df = load_custom_food_offers()
sanitation_df = load_sanitation_data()
helping_out_shelter_df = load_helping_out_shelter_data()
helping_out_support_df = load_helping_out_support_data()
helping_out_hygiene_df = load_helping_out_hygiene_data()

available_filters = build_available_filters(
    osm_df,
    helping_out_food_df,
    custom_food_df,
    sanitation_df,
    helping_out_shelter_df,
    helping_out_support_df,
    helping_out_hygiene_df
)

if not available_filters:
    st.warning("No services found.")
    st.stop()

render_quick_actions()

selected_type, search_term, show_only_phone, show_only_website, show_only_address = render_sidebar(available_filters)

filtered_df = build_filtered_df(
    selected_type,
    osm_df,
    helping_out_food_df,
    custom_food_df,
    sanitation_df,
    helping_out_shelter_df,
    helping_out_support_df,
    helping_out_hygiene_df,
)
filtered_df = filtered_df[
    ~filtered_df["name"].fillna("").str.strip().str.lower().isin(["", "unknown"])
].reset_index(drop=True)
filtered_df = apply_detail_filters(filtered_df, show_only_phone, show_only_website, show_only_address)
filtered_df = dedupe_locations(filtered_df)
filtered_df = apply_search_filter(filtered_df, search_term)

render_metrics(filtered_df)

if filtered_df.empty:
    st.warning("No locations found for this filter.")
    st.stop()

st.write(f"Showing **{len(filtered_df)}** locations")
render_map(filtered_df)
render_results(filtered_df, selected_type)
render_raw_table(filtered_df)