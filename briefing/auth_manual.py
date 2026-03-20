#!/usr/bin/env python3
"""
Autenticação manual via code exchange — sem redirect_uri
"""

import json
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent
CREDENTIALS = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

# Ler credentials
with open(CREDENTIALS) as f:
    creds_data = json.load(f)['installed']

client_id = creds_data['client_id']
client_secret = creds_data['client_secret']

# Passo 1: Gerar authorization URL
redirect_uri = "http://localhost"
scope = "https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/gmail.readonly"

auth_url = (
    f"https://accounts.google.com/o/oauth2/auth?"
    f"client_id={client_id}&"
    f"redirect_uri={redirect_uri}&"
    f"response_type=code&"
    f"scope={scope}&"
    f"access_type=offline"
)

print("=" * 80)
print("🔐 AUTORIZE NO GOOGLE (clique neste link no iPhone):")
print("=" * 80)
print(auth_url)
print("=" * 80)

# Passo 2: Pedir o código
auth_code = input("\n📝 Cole aqui o código que receber (depois de autorizar): ").strip()

if not auth_code:
    print("❌ Nenhum código fornecido.")
    exit(1)

# Passo 3: Trocar código por token
token_url = "https://oauth2.googleapis.com/token"
token_data = {
    "grant_type": "authorization_code",
    "code": auth_code,
    "client_id": client_id,
    "client_secret": client_secret,
    "redirect_uri": "http://localhost",  # Necessário mesmo que não use
}

try:
    resp = requests.post(token_url, data=token_data)
    resp.raise_for_status()
    token = resp.json()
    
    # Salvar token
    TOKEN_FILE.write_text(json.dumps(token, indent=2))
    print("\n✅ Token salvo com sucesso!")
    print(f"   Arquivo: {TOKEN_FILE}")
    
except requests.exceptions.RequestException as e:
    print(f"\n❌ Erro ao obter token: {e}")
    print(f"Resposta: {resp.text}")
    exit(1)
