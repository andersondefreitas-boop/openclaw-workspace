#!/usr/bin/env python3
"""
Ponto de entrada do bot.

Uso:
  cp .env.example .env
  # edite o .env com suas credenciais
  pip install -r requirements.txt
  python run.py
"""

import os
import sys

# Carrega .env manualmente (sem precisar de python-dotenv instalado)
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())
    print("✅ .env carregado")
else:
    print("⚠️  .env não encontrado — usando variáveis de ambiente do sistema")
    print("   Dica: cp .env.example .env  e edite com suas credenciais\n")

# Validação mínima
dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
if not dry_run:
    for var in ["PRIVATE_KEY", "WALLET_ADDRESS", "POLY_API_KEY"]:
        if not os.getenv(var):
            print(f"❌ {var} não configurado. Defina no .env ou use DRY_RUN=true")
            sys.exit(1)

print(f"🚀 Iniciando no modo: {'DRY RUN (simulação)' if dry_run else '⚠️  REAL MONEY'}\n")

from bot import main
main()
