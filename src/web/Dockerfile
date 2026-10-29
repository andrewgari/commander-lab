# SvelteKit (adapter-node) — commander-lab web frontend. Build context is the REPO ROOT (the /dev/review preview imports
# the review-document fixtures under tests/fixtures/review_document):
#   docker build -f src/web/Dockerfile .      (docker compose does this for the `web` service)
FROM node:24-alpine AS build
WORKDIR /repo/src/web
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/web ./
COPY tests/fixtures/review_document /repo/tests/fixtures/review_document
RUN npm run build

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /repo/src/web/build ./build
COPY --from=build /repo/src/web/package.json ./package.json
COPY --from=build /repo/src/web/node_modules ./node_modules
EXPOSE 3000
CMD ["node", "build"]
