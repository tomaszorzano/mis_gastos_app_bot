"""
Punto de entrada que carga .env antes de ejecutar el bot.
Útil en local; en Replit/Render las env vars vienen del panel.
"""
from dotenv import load_dotenv
load_dotenv()

from bot import main

if __name__ == "__main__":
    main()
