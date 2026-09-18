import os
import zipfile
from datetime import datetime
import requests
from config import *
from zoneinfo import ZoneInfo

# --------------------------------
# Ensure folders exist
# --------------------------------

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(MARKER_FOLDER, exist_ok=True)

# --------------------------------
# Weekend Skip
# --------------------------------

today = datetime.now(
    ZoneInfo("Asia/Karachi")
)

if today.weekday() >= 5:
    print("Weekend. Exiting.")
    quit()

# --------------------------------
# Build URL
# --------------------------------

date_str = today.strftime("%Y-%m-%d")

url = f"{BASE_URL}/{date_str}.Z"

print("Checking")
print(url)

# --------------------------------
# Marker Check
# --------------------------------

marker_file = os.path.join(
    MARKER_FOLDER,
    f"{date_str}.done"
)

if os.path.exists(marker_file):
    print("Already processed.")
    quit()

# --------------------------------
# Download
# --------------------------------

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Referer": "https://dps.psx.com.pk/downloads"
}

response = requests.get(
    url,
    headers=headers,
    timeout=60
)

print("Status:", response.status_code)

if response.status_code != 200:
    print("PSX file not uploaded yet.")
    quit()

print("File found.")

# --------------------------------
# Save Downloaded File
# --------------------------------

z_path = os.path.join(
    DOWNLOAD_FOLDER,
    f"{date_str}.Z"
)

with open(z_path, "wb") as f:
    f.write(response.content)

print(f"Downloaded: {z_path}")

# --------------------------------
# Extract ZIP Archive
# --------------------------------

try:

    with zipfile.ZipFile(z_path, "r") as zip_ref:

        print("\nZIP Contents:")

        for member in zip_ref.namelist():

            print(f"  - {member}")

            destination = os.path.join(
                DOWNLOAD_FOLDER,
                member
            )

            # overwrite existing file
            if os.path.exists(destination):
                os.remove(destination)

            with zip_ref.open(member) as source:
                with open(destination, "wb") as target:
                    target.write(source.read())

        print("\nExtraction completed.")

except Exception as e:

    print("ZIP extraction failed:")
    print(str(e))
    quit()

# --------------------------------
# Marker
# --------------------------------

with open(marker_file, "w") as f:
    f.write("done")

print("\nCompleted Successfully.")