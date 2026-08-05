# Virtual Memorial Candle

This Flask website provides:

- Virtual candle lighting
- Optional tribute messages
- A live candle count and memorial wall
- Simple M-Pesa contribution details
- WhatsApp sharing
- Password-protected moderation
- SQLite or PostgreSQL storage

## Personalize

1. Copy `.env.example` to `.env`.
2. Add Mum's photo as `static/mum.jpg`.
3. Edit all memorial and M-Pesa values in `.env`.

## Run locally on Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python app.py
```

Open `http://127.0.0.1:5000`.

Moderation is available at `http://127.0.0.1:5000/admin/login`.

## Run locally on macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Deploy on Render

1. Create a GitHub repository and upload the project.
2. In Render, create a Blueprint from the repository.
3. Render reads `render.yaml`.
4. Enter every environment variable marked `sync: false`.
5. Deploy.
6. Open the generated public URL.
7. Test candle creation and `/admin/login`.

The included Blueprint uses a paid Starter web service and a 1 GB persistent
disk. The SQLite database is saved at `/var/data/memorial.db`.

Without persistent storage, submitted candles disappear after a service restart
or redeployment.

## Final checks

- Replace the placeholder with `static/mum.jpg`.
- Verify Mum's full name, burial details, and memorial message.
- Verify the M-Pesa number and recipient name with a KES 1 test.
- Set a strong `ADMIN_PASSWORD`.
- Light, hide, and delete a test candle.
- Test the WhatsApp button on a phone.
- Let close family review the wording and payment details.
- Share the public link in WhatsApp groups.

## Privacy

The site does not request contributor phone numbers or payment amounts.
It stores only a non-reversible IP hash for a short anti-spam cooldown.
