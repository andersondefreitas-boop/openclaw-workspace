#!/usr/bin/env python3
"""
Gerador de URL de autenticação Google — acesse pelo iPhone
"""

import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).parent
CREDENTIALS = BASE_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

if not CREDENTIALS.exists():
    print("❌ credentials.json não encontrado!")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
auth_url, _ = flow.authorization_url(prompt='consent')

print("=" * 70)
print("🔐 CLIQUE NESTE LINK NO SEU iPHONE:")
print("=" * 70)
print(auth_url)
print("=" * 70)
print("\nDepois que autorizar, volte aqui e cole o código.")
print("=" * 70)

auth_code = input("\n📝 Cole o código de autorização aqui: ").strip()

if auth_code:
    creds = flow.fetch_token(code=auth_code)
    token_file = BASE_DIR / "token.json"
    token_file.write_text(json.dumps(creds))
    print("✅ Token salvo! Agora tá tudo certo.")
else:
    print("❌ Nenhum código fornecido.")
