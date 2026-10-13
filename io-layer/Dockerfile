FROM nginx:alpine

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# The base image ships its own index.html (the "Welcome to nginx" page); this app has none
RUN rm /usr/share/nginx/html/index.html

# Copy static files
COPY user_index.html /usr/share/nginx/html/
COPY admin_index.html /usr/share/nginx/html/
COPY styles.css /usr/share/nginx/html/
COPY common.js /usr/share/nginx/html/
COPY app.js /usr/share/nginx/html/
COPY admin.js /usr/share/nginx/html/
COPY login.html /usr/share/nginx/html/
COPY login.js /usr/share/nginx/html/
COPY theme.js /usr/share/nginx/html/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
