import os
import zipfile
import json
import shutil
from datetime import datetime
from flask import send_file

BACKUP_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'backups')
os.makedirs(BACKUP_DIR, exist_ok=True)

class BackupService:
    @staticmethod
    def get_backup_dir():
        os.makedirs(BACKUP_DIR, exist_ok=True)
        return BACKUP_DIR

    @classmethod
    def create_backup(cls, db_path, uploads_path):
        """
        Δημιουργεί πλήρες αντίγραφο ασφαλείας (Database + Φωτογραφίες Ζημιών + Manifest).
        Επιστρέφει το path του παραγόμενου .zip αρχείου και το μέγεθός του.
        """
        cls.get_backup_dir()
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        zip_filename = f"autostatus_backup_{timestamp}.zip"
        zip_path = os.path.join(BACKUP_DIR, zip_filename)

        manifest = {
            'created_at': datetime.utcnow().isoformat(),
            'app': 'AutoStatus Workshop Pro',
            'version': '1.0.0',
            'files_included': []
        }

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 1. Include Database
            if os.path.exists(db_path):
                zipf.write(db_path, arcname='database/autostatus.db')
                manifest['files_included'].append('database/autostatus.db')

            # 2. Include Uploaded Photos
            if os.path.exists(uploads_path):
                for root, _, files in os.walk(uploads_path):
                    for file in files:
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, uploads_path)
                        zipf.write(full_path, arcname=f"uploads/{rel_path}")
                        manifest['files_included'].append(f"uploads/{rel_path}")

            # 3. Include Manifest
            zipf.writestr('manifest.json', json.dumps(manifest, indent=2))

        file_size_kb = round(os.path.getsize(zip_path) / 1024, 2)
        return {
            'filename': zip_filename,
            'filepath': zip_path,
            'size_kb': file_size_kb,
            'created_at': datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S'),
            'items_count': len(manifest['files_included'])
        }

    @classmethod
    def list_backups(cls):
        """Επιστρέφει τη λίστα όλων των υπαρχόντων αντιγράφων ασφαλείας ταξινομημένα κατά ημερομηνία."""
        cls.get_backup_dir()
        backups = []
        for file in os.listdir(BACKUP_DIR):
            if file.endswith('.zip') and file.startswith('autostatus_backup_'):
                full_path = os.path.join(BACKUP_DIR, file)
                stat = os.stat(full_path)
                backups.append({
                    'filename': file,
                    'size_kb': round(stat.st_size / 1024, 2),
                    'created_at': datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M:%S'),
                    'timestamp': stat.st_mtime
                })
        # Sort newest first
        backups.sort(key=lambda x: x['timestamp'], reverse=True)
        return backups

    @classmethod
    def restore_backup(cls, zip_filepath, target_db_path, target_uploads_path):
        """
        Επαναφέρει τη βάση και τα αρχεία από ένα αντίγραφο ασφαλείας .zip.
        """
        if not os.path.exists(zip_filepath):
            raise FileNotFoundError("Backup file not found")

        temp_extract = os.path.join(BACKUP_DIR, 'temp_restore')
        if os.path.exists(temp_extract):
            shutil.rmtree(temp_extract)
        os.makedirs(temp_extract, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_filepath, 'r') as zipf:
                zipf.extractall(temp_extract)

            # Restore Database
            extracted_db = os.path.join(temp_extract, 'database', 'autostatus.db')
            if os.path.exists(extracted_db):
                shutil.copy2(extracted_db, target_db_path)

            # Restore Uploads
            extracted_uploads = os.path.join(temp_extract, 'uploads')
            if os.path.exists(extracted_uploads):
                os.makedirs(target_uploads_path, exist_ok=True)
                for item in os.listdir(extracted_uploads):
                    s = os.path.join(extracted_uploads, item)
                    d = os.path.join(target_uploads_path, item)
                    if os.path.isfile(s):
                        shutil.copy2(s, d)

            return True
        finally:
            if os.path.exists(temp_extract):
                shutil.rmtree(temp_extract)
