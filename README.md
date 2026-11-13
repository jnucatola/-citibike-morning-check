# Carroll & Columbia Citi Bike e-bike checker

Runs at **7:45 AM Monday–Friday, New York time** using GitHub Actions. Checks live e-bike inventory at Carroll St & Columbia St, finds the closest station with e-bikes when none are there, and includes current temperature, apparent temperature and weather conditions. No API keys are needed for Citi Bike or Open-Meteo.

## Setup

1. Create a **new GitHub repository** (suggested name `citibike-morning-check`) and upload the contents of this folder to its default `main` branch, retaining `.github/workflows/morning.yml`.
2. Open the repository's **Actions** tab, select **Citi Bike morning report**, and use **Run workflow** to test it immediately.
3. See the result under **Actions → latest run → Summary**, or open `reports/latest.md` in the repository. Each day has a dated file in `reports/`.
4. For **iPhone push notifications**, see the setup section below. (Optional) To get a real **email alert**, configure the six repository Actions secrets described below. Without these secrets, the program saves a GitHub report but **does not send a push notification**.

> GitHub Actions can run later than the exact cron time; scheduling at 7:45 AM does not guarantee a notification at precisely 7:45 AM. Scheduled workflows on public repos may be disabled after prolonged inactivity.

### Optional emailed reports

Under **Settings → Secrets and variables → Actions → New repository secret**, add:

| Secret | Meaning |
|---|---|
| `REPORT_EMAIL_TO` | Receiving email address; enables email sending |
| `REPORT_EMAIL_FROM` | Sender email address |
| `SMTP_HOST` | Your SMTP service host |
| `SMTP_PORT` | Usually `465` (optional; defaults to 465) |
| `SMTP_USER` | SMTP login name |
| `SMTP_PASSWORD` | SMTP password / app password |

For Gmail you generally need an app password with two-step verification, and `smtp.gmail.com` on port `465`. Other mail providers have their own settings. **Never commit SMTP passwords to the repository.**

## Data and accuracy

- Inventory: Citi Bike's GBFS `station_information`, `station_status`, and `vehicle_types` feeds. The checker tries the Lyft-operated 2.3 feed then the Citi Bike legacy GBFS feed.
- Only **electric** bikes count as e-bikes. If the feed cannot distinguish electric bikes, it reports unavailable rather than zero.
- A station must report fresh status (20 minutes or newer) and not be marked as unavailable for renting.
- Nearest alternative is selected by **straight-line proximity**, not pedestrian route length. A Google Maps **walking directions** link provides actual walking route information. The program does not invent walking distances.
- Weather: Open-Meteo current modeled conditions for Carroll & Columbia (approximately 40.683046, -74.003486). Weather data is not a street-level thermometer reading.
- Data can change after the scheduled snapshot.
- If the inventory request fails, the run visibly fails and saves a report explaining what happened; optional email can still send the error report.

## Local test

```sh
python3 check.py
python3 -m unittest discover -s tests -v
```

Requires Python 3.11+ and an internet connection. All network calls use standard Python libraries; no package installation needed.

## iPhone push notifications (ntfy)

1. Install the **ntfy** app for iPhone from https://ntfy.sh/ (App Store link there), and allow notifications.
2. Create a long random, hard-to-guess topic, such as a 32+ character random string. Do **not** use a short topic like `citibike`: ntfy.sh topics are publicly accessible to anyone who knows the name.
3. In the ntfy iPhone app, tap **Subscribe**, choose server `https://ntfy.sh`, and enter your exact topic.
4. In this GitHub repo, visit **Settings → Secrets and variables → Actions → New repository secret**. Create a secret called **NTFY_TOPIC** whose value is *only* that topic name (not a URL).
5. Open **Actions → Citi Bike morning report → Run workflow**, then check that the phone receives a notification. The report is sent even when inventory is unavailable.
6. You can turn the push notifications off by deleting `NTFY_TOPIC` from GitHub Secrets.

No ntfy account or API key is required for basic use of the public ntfy.sh server. Anyone with the exact topic name can read and publish notifications to it; choose a long unguessable topic and don't publish private details. ntfy and GitHub Actions may delay notifications; they are not guaranteed to arrive at exactly 7:45 AM.
