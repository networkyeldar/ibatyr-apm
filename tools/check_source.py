"""Fail release builds if common private files accidentally entered the source tree."""
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
blocked=[]
for p in root.rglob('*'):
    if any(x in p.parts for x in ('.git','.venv','.vendor-venv','__pycache__','vendor','packages','.pytest_cache')):continue
    if p.is_file() and (p.suffix=='.pem' or p.name.startswith('users.sqlite3') or p.name in ('.ai_settings.json','settings.json','state.json','license.json','storage.json') or 'activation-request' in p.name):blocked.append(str(p.relative_to(root)))
if blocked:
    print('Remove private files before publishing:',', '.join(blocked));sys.exit(1)
print('No private configuration/key files in source tree. Review content before publishing.')
