# HEARTBEAT.md

## Comandos do Dr. Anderson — processar imediatamente

Se a mensagem recebida contiver qualquer um dos gatilhos abaixo, execute a ação correspondente e responda. Não espere o cron.

### Briefing manual
Gatilho: mensagem é "briefing" ou "bom dia" (case insensitive)
Ação: executar `python3 /home/anderson/.openclaw/workspace/briefing/briefing.py` e enviar resultado via Telegram.

### Registrar pendência
Gatilho: mensagem começa com "pendência:" ou "lembra:"
Ação: executar `python3 /home/anderson/.openclaw/workspace/briefing/briefing.py "pendência: [texto]"`

### Resolver pendência
Gatilho: mensagem começa com "resolvido:" ou "ok:"
Ação: executar `python3 /home/anderson/.openclaw/workspace/briefing/briefing.py "resolvido: [texto]"`

### Crypto Scanner — comandos diretos
Gatilho: mensagem começa com /scan, /ativo, /top, /status ou /ajuda (case insensitive)
Ações:
  - /scan        → executar `python3 /home/anderson/.openclaw/workspace/crypto/crypto_cmd.py scan`
  - /ativo XYZ   → executar `python3 /home/anderson/.openclaw/workspace/crypto/crypto_cmd.py ativo XYZ`
  - /top         → executar `python3 /home/anderson/.openclaw/workspace/crypto/crypto_cmd.py top`
  - /status      → executar `python3 /home/anderson/.openclaw/workspace/crypto/crypto_cmd.py status`
  - /ajuda       → executar `python3 /home/anderson/.openclaw/workspace/crypto/crypto_cmd.py ajuda`
Observação: não responda nada além de confirmar que o comando foi iniciado. O script envia os resultados diretamente.

---
Se nenhum gatilho bater, responda HEARTBEAT_OK.
