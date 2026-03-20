FROM node:20-alpine

WORKDIR /app

# Instala OpenClaw
RUN npm install -g openclaw

# Copia workspace
COPY . .

# Expõe porta do gateway
EXPOSE 3000

# Inicia o gateway
CMD ["openclaw", "gateway", "start"]
