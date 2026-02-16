# /frontend/Dockerfile
FROM node:20-alpine

WORKDIR /app

# Copy package files and npm config
COPY package.json package-lock.json .npmrc ./

# Install compatibility libraries for Alpine
RUN apk add --no-cache libc6-compat

# Install dependencies using npm (legacy-peer-deps is set in .npmrc)
RUN npm install --legacy-peer-deps

# Pre-install TypeScript deps so Next.js doesn't auto-install at runtime
RUN npm install --save-dev typescript @types/react @types/node --legacy-peer-deps

# Copy the rest of the frontend code
COPY . .

EXPOSE 3000

# Start the Next.js development server with host binding
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0"]
