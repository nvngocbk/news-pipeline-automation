import argparse
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SERVICE_ACCOUNT_FILE = '/home/minipc/keys/symbolic-pipe-491806-a8-ce6c0558fdce.json'
DEFAULT_FOLDER_ID = '12u7OAjH7VRcgDS0zWZ9pVrwYY81R7t3p'
SCOPES = ['https://www.googleapis.com/auth/drive']


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Upload a file to Google Drive folder')
    parser.add_argument('file_path', help='absolute path to the file to upload')
    parser.add_argument(
        '--folder-id',
        default=DEFAULT_FOLDER_ID,
        help='Drive folder ID (defaults to the configured folder)'
    )
    parser.add_argument(
        '--mime-type',
        default='video/mp4',
        help='MIME type of the file (default: video/mp4)'
    )
    parser.add_argument(
        '--display-name',
        help='Optional name to use in Drive (defaults to source filename)'
    )
    return parser.parse_args()


def main():
    args = parse_args()
    file_path = Path(args.file_path).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f'File not found: {file_path}')

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    service = build('drive', 'v3', credentials=creds)

    file_metadata = {
        'name': args.display_name or file_path.name,
        'parents': [args.folder_id],
    }
    media = MediaFileUpload(str(file_path), mimetype=args.mime_type, resumable=True)

    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id,name,webViewLink,webContentLink,parents'
    ).execute()

    print(created)


if __name__ == '__main__':
    main()
