FROM node:20-alpine

WORKDIR /app

# Copia package.json e instala dependências
COPY package.json package-lock.json* ./
RUN npm install

# Copia workspace
COPY . .

# Porta
EXPOSE 3000

# Inicia
CMD ["npm", "start"]
