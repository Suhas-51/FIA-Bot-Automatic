import os
import requests
from bs4 import BeautifulSoup
from pdf2image import convert_from_path
from instagrapi import Client
import tempfile

INSTAGRAM_USER = "fia.f1.docs"
INSTAGRAM_PASS = "Verisk@11"
DOCS_URL = "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14/season/season-2025-2071"

def get_docs():
    r = requests.get(DOCS_URL)
    soup = BeautifulSoup(r.text, "html.parser")
    docs = []
    for row in soup.select("div.view-content table tbody tr"):
        cols = row.find_all("td")
        if len(cols) >= 3:
            link = cols[0].find("a", href=True)
            subject = cols[1].text.strip()
            issued = cols[2].text.strip()
            href = link['href']
            if href.endswith(".pdf"):
                docs.append({"subject": subject, "issued": issued, "url": href})
    return docs

def convert_pdf_to_img(url):
    resp = requests.get(url)
    with tempfile.TemporaryDirectory() as d:
        pdf_file = os.path.join(d, "file.pdf")
        with open(pdf_file, "wb") as f:
            f.write(resp.content)
        images = convert_from_path(pdf_file, fmt="jpeg")
        img_files = []
        for i, img in enumerate(images):
            imgname = os.path.join(d, f"p{i}.jpg")
            img.save(imgname)
            img_files.append(imgname)
        return img_files

def post_to_instagram(images, caption):
    cl = Client()
    cl.login(INSTAGRAM_USER, INSTAGRAM_PASS)
    if len(images) > 1:
        cl.album_upload(images, caption)
    else:
        cl.photo_upload(images[0], caption)

def main():
    posted = set()
    if os.path.exists("posted.txt"):
        with open("posted.txt") as f:
            posted = set(l.strip() for l in f)
    docs = get_docs()
    for doc in docs:
        if doc["url"] not in posted:
            imgs = convert_pdf_to_img(doc["url"])
            caption = f"{doc['subject']} ({doc['issued']})"
            post_to_instagram(imgs, caption)
            with open("posted.txt", "a") as f:
                f.write(doc["url"] + "\n")
            break

if __name__ == "__main__":
    main()

