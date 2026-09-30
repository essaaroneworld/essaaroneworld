#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu/Debian VPS: systemd service + nginx + Let's Encrypt + nightly backups.
# Usage (as root, from the pw-exam directory):  sudo ./deploy/install-ubuntu.sh exam.example.com admin@example.com
set -euo pipefail
DOMAIN="${1:?usage: install-ubuntu.sh <domain> <email-for-letsencrypt>}"
EMAIL="${2:?usage: install-ubuntu.sh <domain> <email-for-letsencrypt>}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)"; exit 1; }
apt-get update -y
apt-get install -y python3 nginx certbot python3-certbot-nginx tzdata

id pwexam >/dev/null 2>&1 || useradd --system --home /var/lib/pwexam --shell /usr/sbin/nologin pwexam
install -d -o pwexam -g pwexam -m 750 /var/lib/pwexam /var/backups/pwexam
rm -rf /opt/pwexam && install -d /opt/pwexam
cp -r "$SRC/pwexam" "$SRC/web" /opt/pwexam/

if [ ! -f /etc/pwexam.env ]; then
  ADMIN_PW="$(python3 -c 'import secrets,string;a=string.ascii_letters+string.digits;print("A1"+"".join(secrets.choice(a) for _ in range(12)))')"
  cat > /etc/pwexam.env <<ENV
PWEXAM_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
PWEXAM_ADMIN_PASSWORD=$ADMIN_PW
PWEXAM_TRUST_PROXY=1
PWEXAM_BASE_URL=https://$DOMAIN
PWEXAM_TIMEZONE=Asia/Kolkata
ENV
  chmod 600 /etc/pwexam.env
  echo "Initial admin login: admin / $ADMIN_PW   (change it after first login)"
fi

install -m 644 "$SRC/deploy/pwexam.service" /etc/systemd/system/pwexam.service
systemctl daemon-reload
systemctl enable --now pwexam

sed "s/exam.example.com/$DOMAIN/" "$SRC/deploy/nginx-pwexam.conf" > /etc/nginx/sites-available/pwexam
ln -sf /etc/nginx/sites-available/pwexam /etc/nginx/sites-enabled/pwexam
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect

install -m 755 "$SRC/deploy/backup.sh" /usr/local/bin/pwexam-backup
echo "15 2 * * * pwexam /usr/local/bin/pwexam-backup" > /etc/cron.d/pwexam-backup

echo "Done. Open https://$DOMAIN/"
