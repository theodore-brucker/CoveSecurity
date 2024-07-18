# Stage 1: Build the React application
FROM node:20.12.0 as build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm install

COPY . ./

ARG REACT_APP_BACKEND_URL
ENV REACT_APP_BACKEND_URL=${REACT_APP_BACKEND_URL}
RUN echo "REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL" && export NODE_OPTIONS=--openssl-legacy-provider && npm run build

# Stage 2: Serve the React application
FROM nginx:alpine

COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]





