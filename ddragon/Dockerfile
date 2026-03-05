# Use the lightweight Nginx Alpine image
FROM nginx:alpine

# Remove the default Nginx configuration
RUN rm /etc/nginx/conf.d/default.conf

# Copy our custom configuration file into the container
COPY nginx.conf /etc/nginx/nginx.conf

# Expose port 8080
EXPOSE 8080