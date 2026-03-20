#!/usr/bin/env python3
"""
Carrega o .env e inicia o bot.
Execute: python run.py
"""
import os, sys

# Carrega .env manualmente (sem precisar instalar python-dotenv)
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
    print("⚠️ .env não encontrado — usando variáveis de ambiente do sistema")

from bot import main
main()
