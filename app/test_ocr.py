# test_ocr.py

from PIL import Image
import pytesseract
import requests
from io import BytesIO

#test_image_url = "https://solvit-dev-us.s3.us-east-2.amazonaws.com/valuation//logBookImg1608366451.jpg"
test_image_url = "https://www.pexels.com/photo/person-holding-photo-of-single-tree-at-daytime-1252983/"


try:
    print(f"Fetching image: {test_image_url}")
    response = requests.get(test_image_url)
    response.raise_for_status()

    image = Image.open(BytesIO(response.content))
    text = pytesseract.image_to_string(image)

    print("=== OCR RESULT ===")
    print(text if text.strip() else "[EMPTY TEXT — OCR failed or image unreadable]")

except Exception as e:
    print(f"❌ Error: {e}")
