# Sketch2Cloud frontend (Next.js 16). Dev dependencies are needed to build, so
# NODE_ENV is only switched to production after `next build`.
FROM node:20-slim

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY package*.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

ENV NODE_ENV=production
ENV PORT=8080
EXPOSE 8080
CMD ["npm", "run", "start"]
