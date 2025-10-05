#!/usr/bin/env python3
import os
import re
import json
import time
import shutil
import fitz  # PyMuPDF
import queue
import hashlib
import logging
import pathlib
import datetime as dt
import subprocess
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Season document listings (try current season first, then fallback)
SEASON_URLS = [
    "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14/season/season-2025-2071",
    "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14/season/season-2024-2043",
]

# Files and dirs
STATE_FILE = "posted_ids.json"
OUT_DIR = pathlib.Path("out")
BRANCH = os.getenv("BRANCH", "main")
REPO = os.getenv("GITHUB_REPOSITORY", "")  # owner/repo from GitHub Actions env
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"

# Instagram credentials via env (GitHub Secrets)
IG_USER_ID = os.getenv("IG_USER_ID")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FIA-IG-bot/1.0)"
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_state(posted_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(posted_ids)), f, indent=2)


def fetch_html(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def parse_listing_for_docs(soup, base_url):
    # Flexible extraction: look for links to individual document pages
    # Entries typically show "Doc NN - Title" and "Published on DD.MM.YY HH:MM TZ"
    items = []
    for a in soup.select("a"):
        href = a.get("href", "")
        text = " ".join(a.get_text(strip=True).split())
        if not href or not text:
            continue
        # Document detail pages usually under /document/ or /documents/ paths
        if re.search(r"/document/", href):
            doc_url = urljoin(base_url, href)
            # Try to locate a nearby "Published on" text by walking up to parent
            parent = a.find_parent()
            published_on = None
            if parent:
                txt = " ".join(parent.get_text(" ", strip=True).split())
                m = re.search(r"Published on\s+([0-9]{2}\.[0-9]{2}\.[0-9]{2})\s+([0-9]{2}:[0-9]{2})", txt)
                if m:
                    published_on = f"{m.group(1)} {m.group(2)}"
            items.append({"title": text, "doc_page_url": doc_url, "published": published_on})
    # Deduplicate by URL
    seen = set()
    dedup = []
    for it in items:
        if it["doc_page_url"] not in seen:
            dedup.append(it)
            seen.add(it["doc_page_url"])
    return dedup


def find_latest_docs(max_docs=10):
    for season in SEASON_URLS:
        try:
            soup = fetch_html(season)
            docs = parse_listing_for_docs(soup, season)
            if docs:
                # Prefer newest first if the page is chronological; keep first max_docs
                return docs[:max_docs]
        except Exception as e:
            logging.warning(f"Failed to parse season page {season}: {e}")
    return []


def extract_pdf_url(doc_page_url):
    soup = fetch_html(doc_page_url)
    # Look for direct links to PDFs (FIA often uses /system/files/...pdf)
    for a in soup.select('a[href$=".pdf"]'):
        href = a.get("href", "")
        if href:
            return urljoin(doc_page_url, href)
    # Fallback: search text for .pdf
    for a in soup.find_all("a"):
        if a.get("href", "").lower().endswith(".pdf"):
            return urljoin(doc_page_url, a["href"])
    raise RuntimeError("No PDF found on document page")


def download_file(url, dest_path):
    with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(r.raw, f)


def pdf_first_page_to_png(pdf_path, png_path, dpi=220):
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(0)
        mat = fitz.Matrix(dpi/72.0, dpi/72.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(png_path)
    finally:
        doc.close()


def safe_slug(text):
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"[^A-Za-z0-9\-_.]+", "", text)
    return text[:100] if len(text) > 100 else text


def git_commit_and_push(commit_message):
    # Configure if needed
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)
    subprocess.run(["git", "add", "-A"], check=True)
    # Commit may fail if no changes; ignore in that case
    subprocess.run(["git", "commit", "-m", commit_message], check=False)
    subprocess.run(["git", "push", "origin", BRANCH], check=True)


def post_to_instagram(image_url, caption):
    base = "https://graph.facebook.com/v21.0"
    # Step 1: create container
    media_url = f"{base}/{IG_USER_ID}/media"
    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": IG_ACCESS_TOKEN,
    }
    r = requests.post(media_url, data=payload, timeout=60)
    r.raise_for_status()
    creation_id = r.json().get("id")
    if not creation_id:
        raise RuntimeError(f"No creation_id returned: {r.text}")

    # Step 2: publish
    publish_url = f"{base}/{IG_USER_ID}/media_publish"
    r2 = requests.post(publish_url, data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}, timeout=60)
    r2.raise_for_status()
    return r2.json()


def main():
    assert IG_USER_ID and IG_ACCESS_TOKEN and REPO, "Missing IG_USER_ID, IG_ACCESS_TOKEN, or GITHUB_REPOSITORY"

    posted_ids = load_state()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    candidates = find_latest_docs(max_docs=12)
    if not candidates:
        logging.info("No documents found")
        return

    new_images = []
    new_ids = []

    for doc in candidates:
        uid = hashlib.sha256(doc["doc_page_url"].encode("utf-8")).hexdigest()[:16]
        if uid in posted_ids:
            continue

        try:
            pdf_url = extract_pdf_url(doc["doc_page_url"])
            title = doc["title"]
            published = doc.get("published") or ""

            base_name = f"{safe_slug(title)}-{uid}"
            pdf_path = OUT_DIR / f"{base_name}.pdf"
            png_path = OUT_DIR / f"{base_name}.png"

            logging.info(f"Downloading PDF: {pdf_url}")
            download_file(pdf_url, pdf_path)

            logging.info(f"Converting to PNG: {png_path}")
            pdf_first_page_to_png(str(pdf_path), str(png_path), dpi=220)

            # Track for commit+publish
            new_images.append({"png_path": png_path, "title": title, "published": published, "uid": uid})
            new_ids.append(uid)
        except Exception as e:
            logging.warning(f"Failed processing {doc['doc_page_url']}: {e}")

    if not new_images:
        logging.info("Nothing new to post")
        return

    # Commit and push images first so they are publicly retrievable
    git_commit_and_push(f"Add {len(new_images)} FIA document images")

    # Give CDN a moment
    time.sleep(8)

    # Publish to Instagram
    for item in new_images:
        rel_path = item["png_path"].as_posix()
        image_url = urljoin(RAW_BASE, rel_path)
        caption_bits = []
        if item["title"]:
            caption_bits.append(item["title"])
        if item["published"]:
            caption_bits.append(f"Published: {item['published']}")
        caption = " — ".join(caption_bits) if caption_bits else "FIA document"

        logging.info(f"Posting to Instagram: {image_url}")
        try:
            post_to_instagram(image_url, caption)
            posted_ids.add(item["uid"])
        except Exception as e:
            logging.error(f"Instagram post failed: {e}")

    save_state(posted_ids)
    # Record state in repo for persistence
    git_commit_and_push("Update posted_ids.json")


if __name__ == "__main__":
    main()
