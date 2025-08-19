server {
    listen 80;
    listen [::]:80;
    server_name gr1ffin.com www.gr1ffin.com;

    # Increase proxy timeouts if admin is doing long operations
    proxy_read_timeout 75s;
    proxy_send_timeout 75s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
    }

    # Optional: serve static exports/backups directly if desired
    location /exports/ {
        alias /opt/mmr/exports/;
        autoindex on;
    }
    location /backups/ {
        alias /opt/mmr/backups/;
        autoindex on;
        deny all; # tighten if you don't want public access
    }
}
