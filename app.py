import os
import zipfile
import subprocess
import requests
import glob
import math

# --- Configuration ---
TELEGRAM_API_BASE_URL = "https://api.telegram.org/bot"
MAX_TELEGRAM_CHUNK_MB = 49 # MB
MAX_TELEGRAM_CHUNK_BYTES = MAX_TELEGRAM_CHUNK_MB * 1024 * 1024

# --- Get environment variables ---
telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
run_number = os.environ.get("GITHUB_RUN_NUMBER")
github_workspace = os.environ.get("GITHUB_WORKSPACE")

if not telegram_token or not chat_id:
    print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
    exit(1)

# --- Helper Functions ---
def send_telegram_message(text):
    """Sends a plain text message to Telegram."""
    url = f"{TELEGRAM_API_BASE_URL}{telegram_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        print(f"Telegram message sent: {text}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending Telegram message: {e}")
        exit(1)

def send_telegram_document(file_path, caption=""):
    """Uploads a single document to Telegram."""
    if not os.path.exists(file_path):
        print(f"Error: File not found for upload: {file_path}")
        return False

    url = f"{TELEGRAM_API_BASE_URL}{telegram_token}/sendDocument"
    files = {"document": open(file_path, "rb")}
    payload = {"chat_id": chat_id, "caption": caption}
    
    print(f"Uploading {os.path.basename(file_path)}...")
    try:
        response = requests.post(url, files=files, data=payload, timeout=300) # Increased timeout for large files
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            print(f"Successfully uploaded {os.path.basename(file_path)}")
            return True
        else:
            print(f"Failed to upload {os.path.basename(file_path)}: {result.get("description", "Unknown error")}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error uploading {os.path.basename(file_path)}: {e}")
        return False
    finally:
        files["document"].close() # Ensure file is closed

def package_and_split_files(temp_dir, output_zip_base_name, source_paths, split_bytes):
    """
    Packages specified directories into a single zip,
    then splits it into smaller parts if it exceeds split_bytes.
    Returns a list of file paths ready for upload.
    """
    os.makedirs(temp_dir, exist_ok=True)
    full_zip_path = os.path.join(temp_dir, f"{output_zip_base_name}.zip")

    # Create the initial large zip archive
    try:
        with zipfile.ZipFile(full_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for source_path in source_paths:
                if not os.path.exists(source_path):
                    print(f"Warning: Source path not found, skipping: {source_path}")
                    continue
                # Add directory content recursively
                for root, _, files in os.walk(source_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # arcname is the name inside the zip. Make it relative to the source root
                        arcname = os.path.relpath(file_path, os.path.dirname(source_path) if os.path.isdir(source_path) else os.path.commonpath(source_paths))
                        zf.write(file_path, arcname)
                print(f"Added {source_path} to zip.")
        print(f"Created initial archive: {full_zip_path}")
    except Exception as e:
        print(f"Error creating initial zip: {e}")
        return []

    file_size = os.path.getsize(full_zip_path)
    print(f"Initial zip size: {file_size / (1024 * 1024):.2f} MB")

    files_to_upload = []
    if file_size > split_bytes:
        print(f"Archive is larger than {MAX_TELEGRAM_CHUNK_MB}MB, splitting...")
        # Use zip -s to split the archive
        split_command = [
            "zip",
            "-s",
            f"{MAX_TELEGRAM_CHUNK_MB}m",
            os.path.join(temp_dir, f"{output_zip_base_name}_part.zip"), # Base name for split parts
            full_zip_path
        ]
        try:
            subprocess.run(split_command, check=True, cwd=temp_dir) # Run in temp_dir
            os.remove(full_zip_path) # Remove original large zip
            # Gather all split parts (z01, z02, ..., zip)
            files_to_upload = sorted(glob.glob(os.path.join(temp_dir, f"{output_zip_base_name}_part.z*")))
            print(f"Created {len(files_to_upload)} split parts.")
        except subprocess.CalledProcessError as e:
            print(f"Error splitting zip file: {e}")
            return []
    else:
        files_to_upload.append(full_zip_path)
        print("Archive is within size limit, no splitting needed.")

    return files_to_upload

# --- Main execution ---
send_telegram_message("Starting packaging and upload of build environment files...")

temp_packaging_dir = "telegram_package_temp"
# Paths relative to the runner's home directory or workspace
pub_cache_path = os.path.expanduser("~/.pub-cache")
gradle_caches_path = os.path.expanduser("~/.gradle/caches")
gradle_wrapper_path = os.path.expanduser("~/.gradle/wrapper")
android_project_path = os.path.join(github_workspace, "android") # project-level android folder

source_dirs_to_package = [
    pub_cache_path,
    gradle_caches_path,
    gradle_wrapper_path,
    android_project_path
]

output_zip_name = f"full_build_env_{run_number}"

packaged_files = package_and_split_files(
    temp_packaging_dir,
    output_zip_name,
    source_dirs_to_package,
    MAX_TELEGRAM_CHUNK_BYTES
)

if not packaged_files:
    send_telegram_message("Failed to package and split build environment files.")
    exit(1)

# Upload packaged files to Telegram
send_telegram_message("Uploading packaged build environment in parts...")
all_uploads_successful = True
for i, file_path in enumerate(packaged_files):
    caption_text = f"Build Env ({run_number}) Part {i+1}/{len(packaged_files)}: {os.path.basename(file_path)}"
    if not send_telegram_document(file_path, caption_text):
        all_uploads_successful = False
        break # Stop if any upload fails

if all_uploads_successful:
    send_telegram_message("Successfully uploaded all packaged build environment files.")
else:
    send_telegram_message("WARNING: Some packaged build environment files failed to upload.")

# --- Cleanup ---
print("Cleaning up temporary files...")
subprocess.run(["rm", "-rf", temp_packaging_dir])
print("Cleanup complete.")
