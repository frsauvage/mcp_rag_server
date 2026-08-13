#!/usr/bin/env python3
"""
check_url.py — Test minimal : authentification WAM + accès à la page d'entrée.

Usage :
    python check_url.py

Lit WEB_CRAWL_USER, WEB_CRAWL_LOGIN_URL, PATH_CA depuis .env, demande le mot
de passe en saisie masquée, tente le login, puis récupère l'URL d'entrée
(demandée en argument ou en input) avec le cookie de session obtenu.
Affiche juste assez pour savoir si ça marche, sans rien indexer.
"""
import os
import sys

import requests
import getpass
from dotenv import load_dotenv

load_dotenv(encoding="utf-8")

PATH_CA = os.getenv("PATH_CA")
WEB_CRAWL_USER = os.getenv("WEB_CRAWL_USER")
WEB_CRAWL_LOGIN_URL = os.getenv("WEB_CRAWL_LOGIN_URL")
WEB_CRAWL_JSESSIONID = os.getenv("WEB_CRAWL_JSESSIONID")
WEB_CRAWL_WAM_COOKIE = os.getenv("WEB_CRAWL_WAM_COOKIE")
WEB_CRAWL_OXS_COOKIE = os.getenv("WEB_CRAWL_OXS_COOKIE")
WEB_CRAWL_DOMAIN = os.getenv("WEB_CRAWL_DOMAIN")

AUTH_WALL_MARKERS = (
    "user couldn't be identified",
    "web access management",
    "captcha",
)


def is_auth_wall(text: str) -> bool:
    lowered = text.lower()
    return sum(1 for m in AUTH_WALL_MARKERS if m in lowered) >= 2


def main():

    session = requests.Session()
    if PATH_CA:
        session.verify = PATH_CA

    session.cookies.set("JSESSIONID", WEB_CRAWL_JSESSIONID, domain=WEB_CRAWL_DOMAIN)
    session.cookies.set("OXS-WAM-PROD.P03", WEB_CRAWL_OXS_COOKIE, domain=WEB_CRAWL_DOMAIN)

    entry_url = sys.argv[1] if len(sys.argv) > 1 else input("URL du point d'entrée à tester : ").strip()
    if not entry_url:
        print("❌ Aucune URL fournie")
        sys.exit(1)

    resp_entry = session.get(entry_url, timeout=10)

    print(f"  Status : {resp_entry.status_code}")
    print(f"  Content-Type : {resp_entry.headers.get('Content-Type', '?')}")
    print(f"  Taille contenu : {len(resp_entry.text)} caractères")
    print(resp_entry.text[:5000])

    assert "<title>II.A.5 Module Ethernet - Framework MTG - LAS / SLB - Global Site</title>" in resp_entry.text[:5000]


if __name__ == "__main__":
    main()