#!/bin/bash
# Hadir@SKBT v2 — VPS Deployment Script (CentOS/AlmaLinux)
set -e

APP_DIR="/opt/hadir-skbt"
REPO="https://github.com/Altrabird/hadir-skbt-v2.git"
DOMAIN="hadirskbt.altrabird.click"

echo "=== Hadir@SKBT v2 Deployment ==="

# 1. Install dependencies
echo "[1/7] Installing system packages..."
dnf install -y epel-release
dnf install -y python3 python3-pip nginx git certbot python3-certbot-nginx

# 2. Clone repo
echo "[2/7] Cloning repository..."
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR" && git pull
else
    git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# 3. Install Python packages
echo "[3/7] Installing Python packages..."
pip3 install -r requirements.txt

# 4. Create .env if not exists
if [ ! -f "$APP_DIR/.env" ]; then
    echo "[4/7] Creating .env from template..."
    cp .env.example .env
    # Generate random secret key
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/change-me-to-a-random-string/$SECRET/" .env
    echo ">>> IMPORTANT: Edit /opt/hadir-skbt/.env with your SPREADSHEET_URL"
else
    echo "[4/7] .env already exists, skipping..."
fi

# 5. Setup Nginx
echo "[5/7] Configuring Nginx..."
cp nginx.conf /etc/nginx/conf.d/hadir-skbt.conf
# Remove default server block if it conflicts
rm -f /etc/nginx/conf.d/default.conf 2>/dev/null
nginx -t && systemctl enable nginx && systemctl restart nginx

# 6. Create systemd service
echo "[6/7] Creating systemd service..."
cat > /etc/systemd/system/hadir-skbt.service << 'UNIT'
[Unit]
Description=Hadir@SKBT v2 Gunicorn
After=network.target

[Service]
User=root
WorkingDirectory=/opt/hadir-skbt
ExecStart=/usr/local/bin/gunicorn -w 2 -b 127.0.0.1:5000 --timeout 120 app:app
Restart=always
RestartSec=5
EnvironmentFile=/opt/hadir-skbt/.env

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable hadir-skbt
systemctl restart hadir-skbt

# 7. SSL with Let's Encrypt
echo "[7/7] Setting up SSL..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@altrabird.click --redirect || echo ">>> SSL setup failed. You can run: certbot --nginx -d $DOMAIN"

echo ""
echo "=== Deployment Complete ==="
echo "App running at: https://$DOMAIN"
echo ""
echo "NEXT STEPS:"
echo "1. Copy credentials.json to $APP_DIR/credentials.json"
echo "2. Edit $APP_DIR/.env if needed"
echo "3. Restart: systemctl restart hadir-skbt"
