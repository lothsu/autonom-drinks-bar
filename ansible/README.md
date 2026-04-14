# Ansible — Pi Setup

One command to go from a freshly flashed SD card to a fully running bar kiosk.

## Prerequisites (your dev machine)

```bash
pip install ansible        # or: brew install ansible
```

SSH access to the Pi must work before running the playbook.

---

## First-time setup

### 1 — Flash the SD card

Use **Raspberry Pi Imager** → **Raspberry Pi OS Lite (64-bit)**.  
In the imager settings (gear icon) configure:
- Hostname: `bar-pi` (or whatever you like)
- Enable SSH with your public key
- Wi-Fi SSID + password
- Username: `pi`

### 2 — Find the Pi's IP

```bash
ping bar-pi.local
# or check your router's DHCP table
```

### 3 — Edit inventory

You need to tell Ansible where your Pi is on the network. Open [inventory.ini](inventory.ini) and replace `192.168.1.100` with your Pi's actual IP address.

To find the IP, either:
- Run `ping bar-pi.local` — the IP shows up in brackets, e.g. `64 bytes from 192.168.1.42`
- Or open your router's device list (usually at `192.168.1.1` or `192.168.0.1` in a browser) and look for `bar-pi`

### 4 — Create the vault (secrets)

```bash
cd ansible/
ansible-vault create group_vars/all/vault.yml
```

Enter a vault password when prompted, then add your secrets:

```yaml
vault_secret_key: "long-random-string"       # python3 -c "import secrets; print(secrets.token_hex(32))"
vault_admin_password: "your-admin-password"
vault_cloud_api_key: ""                       # leave empty if sync disabled
vault_bar_uid: ""                             # leave empty if sync disabled
vault_cloud_url: ""                           # leave empty if sync disabled
```

Save the vault password somewhere safe (password manager). You need it every time you run the playbook.

### 5 — Run the playbook

```bash
ansible-playbook -i inventory.ini site.yml --ask-vault-pass
```

That's it. The Pi will:
- Run a full system upgrade
- Install all dependencies
- Clone the app
- Deploy your `.env` with secrets
- Enable the Flask systemd service
- Configure Chromium kiosk autostart
- Enable automatic security updates (nightly, auto-reboot at 3am if needed)

---

## Hardware replacement (Pi dies)

1. Flash a new SD card (same steps as above)
2. Boot it, wait ~60 seconds for SSH to come up
3. Run the playbook again — same command, same vault password

The database (`instance/drinks.db`) is local to the Pi.  
If cloud sync is enabled, transactions are already in the cloud and will re-sync.  
If sync is disabled, keep a periodic manual backup (see below).

---

## Re-running after changes

The playbook is **idempotent** — safe to run again at any time. It only changes what is out of date.

```bash
# Update .env values (e.g. new admin password):
ansible-vault edit group_vars/all/vault.yml
ansible-playbook -i inventory.ini site.yml --ask-vault-pass

# Restart the Flask service manually:
ansible -i inventory.ini bar -m systemd -a "name=autonom-drinks state=restarted" --become
```

---

## Database backup (if sync is disabled)

```bash
# Pull a copy of the DB to your dev machine:
scp pi@bar-pi.local:/home/pi/autonom-drinks-bar/instance/drinks.db ./backups/drinks-$(date +%Y%m%d).db
```

Run this periodically or automate it with a cron job on your dev machine.

---

## Vault password — don't lose it

Without the vault password you cannot re-run the playbook and cannot read the secrets.  
Store it in a password manager (Bitwarden, 1Password, etc.).
