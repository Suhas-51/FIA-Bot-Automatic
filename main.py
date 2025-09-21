#!/usr/bin/env python3
import os
import re
import json
import logging
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from instagrapi import Cl

# CONFIG (read from env)
FIA_INDEX_URL = "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14/"
POSTED_JSON = "posted.json"
GIT_COMMIT_NAME = "github-actions[bot]"
GIT_COMMIT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"

INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD")

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fia-poster")

def get_page_links(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            if href.startswith("/"):
                href = "https://www.fia.com" + href
            elif not href.startswith("http"):
                href = requests.compat.urljoin(url, href)
            links.append({"href": href, "text": a.get_text(strip=True)})
    seen = set()
    result = []
    for l in links:
        if l["href"] not in seen:
            seen.add(l["href"])
            result.append(l)
    return result

def sha1_of_url(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()

def download_pdf(url, target_path):
    logger.info("Downloading %s", url)
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(target_path, "wb") as f:
        for chunk in r.iter_content(1024*8):
            f.write(chunk)
    return target_path

def extract_text_from_pdf(pdf_path, max_pages=2):
    try:
        reader = PdfReader(pdf_path)
        text = []
        for i, p in enumerate(reader.pages[:max_pages]):
            try:
                text.append(p.extract_text() or "")
            except Exception:
                pass
        return "\n".join(text)
    except Exception as e:
        logger.warning("PyPDF2 failed: %s", e)
        return ""

def find_issue_date_and_serial(text, http_last_modified=None):
    date_patterns = [
        r'\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b',
        r'\b(\d{4}-\d{2}-\d{2})\b',
        r'\b([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})\b',
    ]
    issued = None
    for pat in date_patterns:
        match = re.search(r'(Issued|Issue date|Date).{0,50}'+pat, text, re.IGNORECASE)
        if match:
            for g in match.groups()[1:]:
                if g:
                    issued = g
                    break
            if issued: break
    if not issued:
        for pat in date_patterns:
            m = re.search(pat, text)
            if m:
                issued = m.group(1)
                break
    if not issued and http_last_modified:
        issued = http_last_modified
    serial = None
    serial_patterns = [
        r'\b(?:Document No\.?|Doc No\.?|Document Number|Serial|S/N|No\.)\s*[:#]?\s*([A-Za-z0-9\-\/\.]+)',
        r'\bRef\.?\s*[:#]?\s*([A-Za-z0-9\-\/\.]+)'
    ]
    for pat in serial_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            serial = m.group(1).strip()
            break
    return issued, serial

def pdf_first_page_to_image(pdf_path, out_path):
    pages = convert_from_path(pdf_path, dpi=200, first_page=1, last_page=1)
    if not pages:
        raise RuntimeError("No pages from pdf2image")
    pages[0].save(out_path, "JPEG")
    return out_path

def build_caption(issued, doc_name, serial):
    lines = []
    if issued: lines.append(f"Issued: {issued}")
    if doc_name: lines.append(f"Document: {doc_name}")
    if serial: lines.append(f"Serial: {serial}")
    lines.append("\n#FIA #F1Docs")
    return "\n".join(lines)

def load_posted():
    if not os.path.exists(POSTED_JSON):
        return []
    with open(POSTED_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def save_posted(posted_list):
    with open(POSTED_JSON, "w", encoding="utf-8") as f:
        json.dump(posted_list, f, indent=2)

def commit_posted_json():
    os.system("git add posted.json || true")
    os.system("git commit -m \"Update posted.json\" || true")
    os.system("git push || true")

def get_http_last_modified(url):
    try:
        r = requests.head(url, timeout=20)
        if r.ok:
            return r.headers.get("Last-Modified")
    except Exception:
        pass
    return None

def instagram_upload(image_path, caption):
    if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
        raise RuntimeError("Instagram credentials not set in environment")
    cl = cl()
    cl.load_settings("session.json")
    cl.login(user, pass)
    media = cl.photo_upload(image_path, caption)
    logger.info("Uploaded %s", getattr(media, 'pk', 'unknown'))
    return media

def main():
    links = get_page_links(FIA_INDEX_URL)
    posted = load_posted()
    posted_set = set(posted)

    for link in links:
        url = link["href"]
        key = sha1_of_url(url)
        if key in posted_set:
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "doc.pdf")
            try:
                download_pdf(url, pdf_path)
            except Exception as e:
                logger.exception("Download failed: %s", e)
                continue

            text = extract_text_from_pdf(pdf_path)
            lastmod = get_http_last_modified(url)
            issued, serial = find_issue_date_and_serial(text, lastmod)
            doc_name = link.get("text") or Path(url).name

            image_out = os.path.join(tmpdir, "page1.jpg")
            try:
                pdf_first_page_to_image(pdf_path, image_out)
            except Exception as e:
                logger.exception("Image conversion failed: %s", e)
                continue

            caption = build_caption(issued, doc_name, serial)

            try:
                instagram_upload(image_out, caption)
            except Exception as e:
                logger.exception("Instagram upload failed: %s", e)
                continue

            posted.append(key)
            save_posted(posted)
            try:
                commit_posted_json()
            except Exception as e:
                logger.exception("Commit failed: %s", e)

if __name__ == "__main__":
    main()
