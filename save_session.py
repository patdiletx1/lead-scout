#!/usr/bin/env python3
"""
save_session.py — Guarda sesiones de plataformas de empleo para el Auto-Apply Twin.

Uso:
  python3 save_session.py linkedin    # Abre LinkedIn, logueate, cierra cuando termines
  python3 save_session.py getonboard  # Abre GetOnBoard
  python3 save_session.py all         # Todas las plataformas

Después de loguearte en cada plataforma, copiá las sesiones al VPS:
  scp -r .playwright_sessions/* root@91.99.157.147:/root/.hermes/home/quotedme_scraper/.playwright_sessions/

Requisitos locales:
  pip3 install playwright && playwright install chromium
"""

import sys
import os
import argparse
import time

# ── Configuración ─────────────────────────────────────────────────────
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".playwright_sessions")

PLATFORMS = {
    "linkedin": {
        "name": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "landing_url": "https://www.linkedin.com/feed/",
        "instructions": """
🔷 LinkedIn — Pasos:
  1. Ingresá tu email/contraseña de LinkedIn
  2. Si pide verificación 2FA, completala
  3. Esperá a que cargue el feed principal
  4. Volvé a esta terminal y presioná ENTER
        """,
    },
    "getonboard": {
        "name": "GetOnBoard",
        "login_url": "https://www.getonbrd.com/login",
        "landing_url": "https://www.getonbrd.com/dashboard",
        "instructions": """
🟢 GetOnBoard — Pasos:
  1. Ingresá tu email/contraseña
  2. Esperá a que cargue el dashboard
  3. Volvé a esta terminal y presioná ENTER
        """,
    },
    "greenhouse": {
        "name": "Greenhouse",
        "login_url": "https://app.greenhouse.io/users/sign_in",
        "landing_url": "https://app.greenhouse.io/dashboard",
        "instructions": """
🟡 Greenhouse — Pasos:
  1. Ingresá tus credenciales
  2. Si no tenés cuenta, este es para ATS al que postulás
  3. Volvé a esta terminal y presioná ENTER
        """,
    },
    "lever": {
        "name": "Lever",
        "login_url": "https://jobs.lever.co/",
        "landing_url": "https://jobs.lever.co/",
        "instructions": """
🟠 Lever — Pasos:
  1. Normalmente no requiere login (es el ATS del empleador)
  2. Solo navegá a la página para guardar cookies
  3. Volvé a esta terminal y presioná ENTER
        """,
    },
}


def save_session(platform_key: str):
    """Open browser, let user log in, save session state."""
    if platform_key not in PLATFORMS:
        print(f"❌ Plataforma '{platform_key}' no reconocida.")
        print(f"   Disponibles: {', '.join(PLATFORMS.keys())}")
        return False

    plat = PLATFORMS[platform_key]
    print(f"\n{'='*60}")
    print(f"🔐 Guardando sesión de {plat['name']}")
    print(f"{'='*60}")
    print(plat["instructions"])

    # Create session directory
    platform_dir = os.path.join(SESSION_DIR, platform_key)
    os.makedirs(platform_dir, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n❌ Playwright no instalado. Ejecutá:")
        print("   pip3 install playwright && playwright install chromium")
        return False

    with sync_playwright() as p:
        # Launch browser VISIBLE (not headless)
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )

        # Use persistent context so cookies/localStorage are saved automatically
        context = p.chromium.launch_persistent_context(
            user_data_dir=platform_dir,
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
            no_viewport=True,
        )

        page = context.new_page()
        page.goto(plat["login_url"], wait_until="networkidle", timeout=30000)

        print(f"\n🌐 {plat['name']}: navegador abierto en {plat['login_url']}")
        print("   Logueate manualmente. Cuando termines, presioná ENTER en esta terminal...")
        input()

        # Save storage state explicitly
        state_file = os.path.join(platform_dir, "state.json")
        context.storage_state(path=state_file)

        # Also verify we're logged in by checking cookies
        cookies = context.cookies()
        print(f"   Cookies guardadas: {len(cookies)}")

        context.close()

        print(f"\n✅ Sesión de {plat['name']} guardada en: {state_file}")
        print(f"   Directorio: {platform_dir}")
        return True


def main():
    parser = argparse.ArgumentParser(description="Guardar sesiones para Auto-Apply Twin")
    parser.add_argument(
        "platform",
        nargs="?",
        default="linkedin",
        choices=["all"] + list(PLATFORMS.keys()),
        help="Plataforma a loguear (default: linkedin)",
    )
    args = parser.parse_args()

    if args.platform == "all":
        for key in PLATFORMS:
            success = save_session(key)
            if not success:
                print(f"⚠️  {key}: falló, continuando con la siguiente...")
    else:
        save_session(args.platform)

    print(f"\n{'='*60}")
    print("📤 Para subir las sesiones al VPS:")
    print(f"   scp -r {SESSION_DIR}/* root@91.99.157.147:/root/.hermes/home/quotedme_scraper/.playwright_sessions/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
