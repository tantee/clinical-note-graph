FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173

# Vite log level via env (info | warn | error | silent). Defaults to info.
# Set VITE_DEBUG=1 to additionally turn on Node debug() output for every
# Vite namespace (DEBUG=vite:*) — useful when chasing host-header rejects,
# resolve failures, or HMR drops. Both vars come from .env via compose.
CMD ["sh", "-c", "[ \"$VITE_DEBUG\" = \"1\" ] && export DEBUG='vite:*'; exec npm run dev -- --host 0.0.0.0 --logLevel ${VITE_LOG_LEVEL:-info}"]
