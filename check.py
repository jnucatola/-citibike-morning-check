#!/usr/bin/env python3
"""Citi Bike e-bike checker. Python 3.11+, no third-party dependencies."""
from __future__ import annotations
import datetime as dt
import json
import math
import os
from pathlib import Path
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlencode, urljoin
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

STATION_ID = '66de44ba-0aca-11e7-82f6-3863bb44ef7c'
STATION_NAME = 'Carroll St & Columbia St'
DEFAULT_LAT, DEFAULT_LON = 40.683046, -74.003486
DISCOVERY_URLS = ['https://gbfs.citibikenyc.com/gbfs/gbfs.json', 'https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json']
TZ = ZoneInfo('America/New_York')
MAX_AGE_SECONDS = 5 * 60

def feed_urls(catalog, catalog_url):
    data = catalog.get('data', {})
    if 'feeds' in data:
        feeds = data['feeds']
    else:
        locale = data.get('en') or data.get('en-US')
        if locale is None:
            locale = next((v for v in data.values() if isinstance(v, dict) and 'feeds' in v), {})
        feeds = locale.get('feeds', [])
    found = {entry['name']: urljoin(catalog_url, entry['url']) for entry in feeds
             if isinstance(entry, dict) and entry.get('name') and entry.get('url')}
    if 'station_information' not in found or 'station_status' not in found:
        raise ValueError('GBFS catalog is missing required station feeds')
    return found


def get_json(url: str) -> dict:
    req = Request(url, headers={'User-Agent': 'CarrollColumbiaBikeChecker/1.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def load_feeds():
    errors = []
    for catalog_url in DISCOVERY_URLS:
        for attempt in range(3):
            try:
                urls = feed_urls(get_json(catalog_url), catalog_url)
                info = get_json(urls['station_information'])
                status = get_json(urls['station_status'])
                types = get_json(urls['vehicle_types']) if 'vehicle_types' in urls else {'data': {'vehicle_types': []}}
                return (info['data']['stations'], status['data']['stations'],
                        types.get('data', {}).get('vehicle_types', []), catalog_url)
            except (ValueError, KeyError, OSError, TypeError) as ex:
                errors.append(str(ex))
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
    raise RuntimeError('Unable to verify official live GBFS inventory: ' + '; '.join(errors[-6:]))


def electric_type_ids(vehicle_types):
    return {str(v['vehicle_type_id']) for v in vehicle_types
            if v.get('propulsion_type', '').lower() in ('electric_assist', 'electric')}


def ebike_count(record, electric_ids):
    # GBFS deployments may expose a direct e-bike count or counts by vehicle type.
    direct = record.get('num_ebikes_available')
    if isinstance(direct, int) and not isinstance(direct, bool) and direct >= 0:
        return direct
    by_type = record.get('vehicle_types_available')
    if isinstance(by_type, list) and electric_ids:
        return sum(int(x.get('count', 0)) for x in by_type
                   if str(x.get('vehicle_type_id')) in electric_ids)
    return None  # unknown must never be treated as zero


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def healthy(station, now):
    if station.get('is_renting') not in (True, 1) or station.get('is_installed') not in (True, 1):
        return False
    reported = station.get('last_reported')
    return isinstance(reported, (int, float)) and 0 <= now.timestamp() - reported <= MAX_AGE_SECONDS


def select_stations(info, statuses, types, now):
    infos = {str(s['station_id']): s for s in info}
    states = {str(s['station_id']): s for s in statuses}
    primary_info = infos.get(STATION_ID)
    primary = states.get(STATION_ID)
    if not primary or not primary_info:
        raise RuntimeError('Carroll & Columbia station missing from current Citi Bike feed')
    if primary.get('is_renting') not in (True, 1) or primary.get('is_installed') not in (True, 1):
        raise RuntimeError('Carroll & Columbia station is unavailable for rentals')
    if not healthy(primary, now):
        raise RuntimeError('Carroll & Columbia status is stale or missing')
    electric_ids = electric_type_ids(types)
    count = ebike_count(primary, electric_ids)
    if count is None:
        raise RuntimeError('Feed does not expose a verifiable electric-bike count')
    available = primary.get('num_bikes_available')
    lat, lon = float(primary_info['lat']), float(primary_info['lon'])
    alternative = None
    if count == 0:
        candidates = []
        for sid, record in states.items():
            meta = infos.get(sid)
            if sid == STATION_ID or not meta or not healthy(record, now):
                continue
            num = ebike_count(record, electric_ids)
            if num is None or num <= 0:
                continue
            dist = haversine_m(lat, lon, float(meta['lat']), float(meta['lon']))
            candidates.append((dist, meta, num))
        if candidates:
            dist, meta, num = min(candidates, key=lambda item: item[0])
            # A route distance would require a separate pedestrian routing service.
            # Report straight-line measurement; do not mislabel as walking distance.
            destination = f"{meta['lat']},{meta['lon']}"
            origin = f'{lat},{lon}'
            alternative = dict(name=meta['name'], ebikes=num, straight_line_m=round(dist),
                               google_walking_directions='https://www.google.com/maps/dir/?' + urlencode({
                                   'api': '1', 'origin': origin, 'destination': destination, 'travelmode': 'walking'}))
    return dict(station=primary_info['name'], station_id=STATION_ID, ebikes=count,
                total_bikes=available, last_reported=primary['last_reported'], is_renting=True, alternative=alternative)


WEATHER_CODES = {
    0:'Clear',1:'Mainly clear',2:'Partly cloudy',3:'Overcast',45:'Fog',48:'Rime fog',
    51:'Light drizzle',53:'Drizzle',55:'Dense drizzle',61:'Light rain',63:'Rain',65:'Heavy rain',
    71:'Light snow',73:'Snow',75:'Heavy snow',80:'Rain showers',81:'Rain showers',
    82:'Heavy showers',95:'Thunderstorm',96:'Thunderstorm with hail',99:'Thunderstorm with hail'
}


def fetch_weather():
    args = urlencode({'latitude': DEFAULT_LAT, 'longitude': DEFAULT_LON,
        'current': 'temperature_2m,apparent_temperature,weather_code',
        'temperature_unit': 'fahrenheit', 'timezone': 'America/New_York'})
    data = get_json('https://api.open-meteo.com/v1/forecast?' + args)['current']
    return {'temperature_f': data['temperature_2m'], 'feels_like_f': data['apparent_temperature'],
            'conditions': WEATHER_CODES.get(data.get('weather_code'), f"Weather code {data.get('weather_code')}") ,
            'observed_time': data.get('time'), 'source': 'Open-Meteo current model conditions'}


def report_md(now, bike, weather, errors):
    date = now.astimezone(TZ).strftime('%A, %B %-d, %Y · %-I:%M %p %Z')
    lines = [f'# Citi Bike morning report — {date}', '', f'**Station:** {STATION_NAME}, Brooklyn', '']
    if bike:
        lines += [f"**E-bikes available:** {bike['ebikes']}",
                  f"**All bikes available:** {bike['total_bikes'] if bike['total_bikes'] is not None else 'Unknown'}",
                  '**Rental status:** Renting',
                  f"**Station status timestamp:** {dt.datetime.fromtimestamp(bike['last_reported'], TZ).isoformat()}", '']
        if bike['ebikes'] == 0:
            alt = bike['alternative']
            if alt:
                lines += [f"**Nearest station with e-bikes:** {alt['name']} — {alt['ebikes']} e-bike(s)",
                          f"**Distance:** {alt['straight_line_m']} m straight-line (not walking distance)",
                          f"[Walking directions]({alt['google_walking_directions']})", '']
            else:
                lines += ['**Alternative:** No verified station with e-bikes found in the available feed.', '']
    else:
        lines += ['**E-bikes available:** Unable to verify', '**All bikes available:** Unable to verify',
                  '**Rental status:** Unverified or unavailable',
                  '**Nearest alternative:** Cannot determine without live inventory', '']
    if weather:
        lines += [f"**Weather:** {weather['temperature_f']}°F · Feels like {weather['feels_like_f']}°F · {weather['conditions']}",
                  f"*Weather source: {weather['source']} ({weather['observed_time']}).*", '']
    else:
        lines += ['**Weather:** Unable to verify', '']
    if errors:
        lines += ['**Data issues:** ' + '; '.join(errors), '']
    lines += ['*Availability is a snapshot and can change before arrival.*', '']
    return '\n'.join(lines)


def send_email(subject, body):
    recipient = os.getenv('REPORT_EMAIL_TO')
    if not recipient:
        return False
    required = ['SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'REPORT_EMAIL_FROM']
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError('Email enabled, but missing settings: ' + ', '.join(missing))
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, os.environ['REPORT_EMAIL_FROM'], recipient
    msg.set_content(body)
    host = os.environ['SMTP_HOST']
    port = int(os.getenv('SMTP_PORT') or '465')
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
            smtp.send_message(msg)
    return True



def send_iphone_push(bike, weather, errors):
    """Publish one concise iPhone alert via ntfy when NTFY_TOPIC is configured."""
    topic = os.getenv('NTFY_TOPIC', '').strip()
    if not topic:
        return False
    # Topics are URL path segments, not full URLs.
    if not (8 <= len(topic) <= 128 and all(c.isascii() and (c.isalnum() or c in '-_') for c in topic)):
        raise ValueError('NTFY_TOPIC must be 8-128 characters: letters, digits, - or _')
    if bike:
        message = f"Carroll & Columbia: {bike['ebikes']} e-bike(s); {bike['total_bikes']} total bikes."
        if bike['ebikes'] == 0:
            alt = bike.get('alternative')
            message += (f" Closest with e-bikes: {alt['name']} ({alt['ebikes']}), "
                        f"~{alt['straight_line_m']}m straight-line." if alt else
                        " No verified alternative found.")
    else:
        message = "Carroll & Columbia e-bike inventory could not be verified."
    if weather:
        message += (f" Weather: {weather['temperature_f']}°F, "
                    f"feels {weather['feels_like_f']}°F; {weather['conditions']}.")
    if errors:
        message += " Some live data could not be retrieved."
    headers = {'Title': 'Citi Bike morning report', 'Priority': 'default',
               'Content-Type': 'text/plain; charset=utf-8'}
    if bike and bike['ebikes'] == 0 and bike.get('alternative'):
        headers['Click'] = bike['alternative']['google_walking_directions']
    else:
        headers['Click'] = 'https://github.com/jnucatola/-citibike-morning-check/blob/main/reports/latest.md'
    request = Request('https://ntfy.sh/' + topic, data=message.encode('utf-8'),
                      headers=headers, method='POST')
    with urlopen(request, timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f'ntfy returned status {response.status}')
    return True


def main():
    now = dt.datetime.now(dt.timezone.utc)
    errors = []
    bike = weather = None
    try:
        info, statuses, types, url = load_feeds()
        bike = select_stations(info, statuses, types, now)
    except Exception as ex:
        errors.append('Citi Bike: ' + str(ex))
    try:
        weather = fetch_weather()
    except Exception as ex:
        errors.append('Weather: ' + str(ex))
    report = report_md(now, bike, weather, errors)
    print(report)
    Path('reports').mkdir(exist_ok=True)
    Path('reports/latest.md').write_text(report, encoding='utf-8')
    stamp = now.astimezone(TZ).strftime('%Y-%m-%d')
    Path(f'reports/{stamp}.md').write_text(report, encoding='utf-8')
    Path('reports/latest.json').write_text(json.dumps({
        'checked_at_utc': now.isoformat(), 'bike': bike, 'weather': weather, 'errors': errors
    }, indent=2) + '\n', encoding='utf-8')
    with open(os.getenv('GITHUB_STEP_SUMMARY', os.devnull), 'a', encoding='utf-8') as f:
        f.write(report)
    send_iphone_push(bike, weather, errors)
    send_email(f"Citi Bike: {bike['ebikes'] if bike else 'unknown'} e-bikes at Carroll & Columbia", report)
    # Fail if inventory fails, so the GitHub workflow signals an error; report still saved.
    if bike is None:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
