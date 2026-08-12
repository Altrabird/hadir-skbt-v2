# Panduan Arahan — Hadir@SKBT v2

Rujukan arahan untuk menyelenggara sistem di VPS.

| Perkara | Nilai |
|---|---|
| Domain | `hadirskbt.altrabird.click` |
| Folder aplikasi | `/opt/hadir-skbt` |
| Perkhidmatan systemd | `hadir-skbt` |
| Gunicorn | 2 worker, `127.0.0.1:5000` |
| Konfigurasi nginx | `/etc/nginx/conf.d/hadir-skbt.conf` |
| Tetapan rahsia | `/opt/hadir-skbt/.env` |
| Tetapan runtime | `/opt/hadir-skbt/bot_settings.json` |

---

## 1. Log masuk ke VPS

```bash
ssh root@hadirskbt.altrabird.click
```

---

## 2. Kemas kini sistem (paling kerap digunakan)

Selepas kod baharu di-push ke GitHub:

```bash
cd /opt/hadir-skbt && git pull && systemctl restart hadir-skbt
```

Semak ia benar-benar hidup semula:

```bash
systemctl status hadir-skbt --no-pager
```

Cari perkataan `active (running)` berwarna hijau.

> **Jika `git pull` kata "Already up to date."** bermakna kod di VPS sudah terkini.
> Sahkan versi yang sedang berjalan:
> ```bash
> cd /opt/hadir-skbt && git log --oneline -1
> ```
> Bandingkan dengan commit terkini di GitHub.

---

## 3. Menukar tetapan `.env`

Lihat tetapan semasa (kata laluan akan kelihatan — jangan kongsi skrin):

```bash
cat /opt/hadir-skbt/.env
```

### Tukar kata laluan guru

```bash
sed -i 's|^SETTINGS_PASSWORD=.*|SETTINGS_PASSWORD=KataLaluanBaharu|' /opt/hadir-skbt/.env
systemctl restart hadir-skbt
```

Gantikan `KataLaluanBaharu` dengan pilihan tuan. **Elakkan ruang kosong dan tanda petik.**

### Tambah tetapan yang belum ada

```bash
cat >> /opt/hadir-skbt/.env <<'EOF'
PUBLIC_BASE_URL=https://hadirskbt.altrabird.click
SESSION_COOKIE_SECURE=1
SCHOOL_NAME=SEKOLAH KEBANGSAAN BANDAR TAWAU
EOF
systemctl restart hadir-skbt
```

### Senarai tetapan

| Kunci | Kegunaan |
|---|---|
| `SECRET_KEY` | Menandatangani kuki sesi dan pautan borang. **Jangan tukar** melainkan perlu — menukarnya membatalkan semua pautan borang dan menyebabkan semua guru log keluar. |
| `SETTINGS_PASSWORD` | Kata laluan kongsi guru |
| `PUBLIC_BASE_URL` | Alamat dalam pautan yang dihantar kepada ibu bapa |
| `SESSION_COOKIE_SECURE` | `1` bila HTTPS aktif. **Jangan** set jika masih HTTP — guru tidak akan boleh log masuk |
| `SPREADSHEET_URL` | Google Sheet sumber data |
| `SCHOOL_NAME` | Nama pada surat amaran |
| `TELEGRAM_BOT_TOKEN` | Kosongkan untuk mematikan notifikasi Telegram |

---

## 4. Melihat log

Log langsung (tekan `Ctrl+C` untuk keluar):

```bash
journalctl -u hadir-skbt -f
```

50 baris terakhir:

```bash
journalctl -u hadir-skbt -n 50 --no-pager
```

Ralat sahaja, sejak semalam:

```bash
journalctl -u hadir-skbt --since yesterday -p err --no-pager
```

---

## 5. Semakan kesihatan

Laman hidup?

```bash
curl -sI https://hadirskbt.altrabird.click | head -1
```

Sepatutnya `HTTP/1.1 200 OK` atau `HTTP/2 200`.

Kod terkini sedang berjalan? (sepatutnya `401`, bukan `404`)

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://hadirskbt.altrabird.click/api/kelas/senarai-saya
```

Sambungan ke Google Sheet berfungsi?

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://hadirskbt.altrabird.click/api/classes
```

---

## 6. Sijil SSL

Semak tarikh luput:

```bash
certbot certificates
```

Perbaharui secara manual (biasanya automatik):

```bash
certbot renew --dry-run   # ujian dahulu
certbot renew             # sebenar
systemctl reload nginx
```

Jika SSL belum dipasang langsung:

```bash
certbot --nginx -d hadirskbt.altrabird.click
```

Selepas SSL aktif, barulah tambah `SESSION_COOKIE_SECURE=1` ke `.env`.

---

## 7. Sandaran `bot_settings.json` ⚠️

Fail ini menyimpan **kod pautan borang**, paras surat amaran, dan tetapan notifikasi.
Ia **tiada dalam git**. Jika hilang, semua pautan yang sudah dihantar ke group kelas akan mati.

Sandar:

```bash
cp /opt/hadir-skbt/bot_settings.json /root/bot_settings.$(date +%F).json
```

Sandaran automatik setiap hari:

```bash
(crontab -l 2>/dev/null; echo "0 2 * * * cp /opt/hadir-skbt/bot_settings.json /root/bot_settings.\$(date +\%F).json") | crontab -
```

Pulihkan:

```bash
cp /root/bot_settings.2026-08-09.json /opt/hadir-skbt/bot_settings.json
systemctl restart hadir-skbt
```

---

## 8. nginx

Uji konfigurasi sebelum reload (sentiasa lakukan ini):

```bash
nginx -t && systemctl reload nginx
```

---

## 9. Masalah lazim

### Guru tidak boleh log masuk

1. Semak kata laluan dalam `.env`:
   ```bash
   grep SETTINGS_PASSWORD /opt/hadir-skbt/.env
   ```
2. Jika `SESSION_COOKIE_SECURE=1` tetapi laman masih HTTP, kuki digugurkan. Sama ada pasang SSL, atau buang tetapan itu buat sementara.
3. Nama guru mesti wujud dalam lajur A tab `REKOD LOG TETAPAN`.

### Guru nampak "Anda belum ditetapkan sebagai guru kelas"

Lajur B tab `REKOD LOG TETAPAN` masih kosong untuk guru itu.
Buka **Tetapan → Guru Kelas** dalam sistem dan tetapkan kelasnya.

Nilai yang boleh diisi dalam lajur B:

| Nilai | Kesan |
|---|---|
| `2 KRITIS` | guru kelas bagi satu kelas |
| `2 KRITIS, 3 KREATIF` | lebih daripada satu kelas |
| `SEMUA` | pentadbir — semua kelas, paras amaran, tetapan guru |
| *(kosong)* | tiada akses kepada rekod kelas, tetapi **masih boleh** buka Daftar Sebab |

### Tab Daftar Sebab

Daftar Sebab memaparkan semua rekod ketidakhadiran dan sebab bagi **seluruh
sekolah**, dan terbuka kepada mana-mana guru yang log masuk — tiada tetapan
diperlukan. Guru bertugas yang bergilir tidak perlu didaftarkan.

Kawalan kelas kekal pada tab **Rekod**, **Surat Amaran** dan **Tetapan**:
di situ guru hanya nampak kelas yang ditetapkan kepadanya.

### Pautan borang kata "tidak sah atau telah tamat tempoh"

- Pautan lebih 2 bulan → hantar semula pautan baharu
- Butang **Jana Pautan Baharu** pernah ditekan → semua pautan lama mati
- `SECRET_KEY` bertukar → semua pautan mati
- `bot_settings.json` hilang → lihat bahagian 7

### Halaman lambat

Muat pertama selepas restart mengambil ~5 saat kerana membaca seluruh Google Sheet.
Muat seterusnya laju kerana ada cache 5 minit. Ini normal.

### Sistem langsung tidak boleh dibuka

```bash
systemctl status hadir-skbt --no-pager
journalctl -u hadir-skbt -n 50 --no-pager
systemctl restart hadir-skbt
```

Jika masih gagal, semak fail kredensial masih ada:

```bash
ls -l /opt/hadir-skbt/credentials.json
```

---

## 10. Kembali ke versi sebelumnya

Jika kemas kini menyebabkan masalah:

```bash
cd /opt/hadir-skbt
git log --oneline -5              # cari commit yang baik
git checkout <sha-commit>
systemctl restart hadir-skbt
```

Kembali ke versi terkini:

```bash
cd /opt/hadir-skbt && git checkout main && git pull && systemctl restart hadir-skbt
```

---

## 11. Yang JANGAN dibuat

- Jangan `git add` fail `credentials.json`, `merged_sheet1.csv`, atau sandaran Excel — repo ini **awam** dan fail tersebut mengandungi kunci peribadi serta data murid
- Jangan padam `bot_settings.json`
- Jangan tukar `SECRET_KEY` pada sistem yang sedang digunakan
- Jangan set `SESSION_COOKIE_SECURE=1` sebelum HTTPS aktif
