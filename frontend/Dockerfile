FROM node:20-slim

WORKDIR /app

# Copy package files first for better layer caching
COPY package*.json ./

# Install build dependencies for node-gyp
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    make \
    g++ \
    build-essential \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libgif-dev \
    librsvg2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN npm install

# Copy the frontend code
COPY . .

# Declare build arguments
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_API_URL # Add this line

# Set environment variables from build arguments
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL # Add this line

ENV NEXT_PUBLIC_VERCEL_ENV production

RUN npm run build

EXPOSE 3000

# Default command is dev, but can be overridden in docker-compose
CMD ["npm", "start"]