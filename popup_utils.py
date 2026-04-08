from pathlib import Path
import pandas as pd

POPUP_TEMPLATE = Path("templates/popup.html").read_text(encoding="utf-8")

def clean(value, fallback):
    return fallback if pd.isna(value) or str(value).strip() == "" else str(value)

def make_website_html(website: str) -> str:
    website = clean(website, "No website listed")
    if website == "No website listed":
        return website
    website = website.strip()
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return f'<a href="{website}" target="_blank">{website}</a>'

def build_popup_html(row) -> str:
    source = clean(row.get("source"), "")

    if source == "City of Melbourne Public Toilets":
        return f"""
        <div style="min-width:220px;">
            <h4 style="margin-bottom:6px;">{clean(row.get("name"), "Public Toilet")}</h4>
            <p style="margin:0;"><strong>Type:</strong> {clean(row.get("type"), "Sanitation")}</p>
        </div>
        """

    return POPUP_TEMPLATE.format(
        name=clean(row.get("name"), "Unknown"),
        type=clean(row.get("type"), "Unknown"),
        address=clean(row.get("address"), "No address listed"),
        phone=clean(row.get("phone"), "No phone listed"),
        website_html=make_website_html(row.get("website")),
        hours=clean(row.get("hours"), "Not listed"),
        transport=clean(row.get("public_transport"), "Not listed"),
        source=clean(row.get("source"), "Source not listed"),
        notes=clean(row.get("notes"), "Not listed"),
    )