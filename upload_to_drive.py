from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SERVICE_ACCOUNT_FILE = '/home/minipc/keys/symbolic-pipe-491806-a8-ce6c0558fdce.json'
FOLDER_ID = '12u7OAjH7VRcgDS0zWZ9pVrwYY81R7t3p'
FILE_PATH = '/home/minipc/.openclaw/workspace/output-veo-cybercat-hanoi.mp4'
SCOPES = ['https://www.googleapis.com/auth/drive']

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
service = build('drive', 'v3', credentials=creds)

file_metadata = {
    'name': Path(FILE_PATH).name,
    'parents': [FOLDER_ID],
}
media = MediaFileUpload(FILE_PATH, mimetype='video/mp4', resumable=True)

created = service.files().create(
    body=file_metadata,
    media_body=media,
    fields='id,name,webViewLink,webContentLink,parents'
).execute()

print(created)
