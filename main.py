#!/usr/bin/env python3
import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from pdf2image import convert_from_path
from instagrapi import Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fia-poster")

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

BASE_URL = "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14/"


def fetch_documents():
    """Scrape FIA documents page and collect (name, link) pairs for PDFs."""
    res = requests.get(BASE_URL)
    soup = BeautifulSoup(res.text, "html.parser")
    docs = []
    for a in soup.select("a[href$='.pdf']"):
        link = a["href"]
        if not link.startswith("http"):
            link = "https://www.fia.com" + link
        name = a.get_text(strip=True)
        docs.append((name, link))
    return docs


def pdf_to_image(pdf_url, out_path="output.jpg"):
    """Download first page of a PDF as an image."""
    r = requests.get(pdf_url)
    pdf_file = "temp.pdf"
    with open(pdf_file, "wb") as f:
        f.write(r.content)
    images = convert_from_path(pdf_file, first_page=1, last_page=1)
    images[0].save(out_path, "JPEG")
    return out_path


def instagram_upload(image_path, caption):
    """Upload an image with caption to Instagram, using session if available."""
    client = Client()

    settings_json = os.getenv("INSTAGRAM_SETTINGS")
    if settings_json:
        try:
            settings = json.loads(settings_json)
            client.set_settings(settings)
            client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            logger.info("✅ Logged in with saved session")
        except Exception as e:
            logger.warning(f"⚠️ Session login failed, falling back: {e}")
            client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    else:
        logger.warning("⚠️ No INSTAGRAM_SETTINGS found, using username/password login")
        client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

    client.photo_upload(image_path, caption)

    # Save updated session so you can refresh your GitHub secret if needed
    new_settings = client.get_settings()
    logger.info("💾 Copy this JSON into your INSTAGRAM_SETTINGS secret to keep session valid:")
    logger.info(json.dumps(new_settings))


def main():
    docs = fetch_documents()
    if not docs:
        logger.error("No documents found.")
        return

    # Take the first document (latest on the FIA page)
    name, link = docs[0]
    logger.info(f"📥 Downloading {link}")
    image_out = pdf_to_image(link)
    caption = f"{name}\nSource: FIA"

    instagram_upload(image_out, caption)


if __name__ == "__main__":
    main()
