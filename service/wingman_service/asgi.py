"""ASGI entry point: uvicorn wingman_service.asgi:app"""

from .main import create_app

app = create_app()
