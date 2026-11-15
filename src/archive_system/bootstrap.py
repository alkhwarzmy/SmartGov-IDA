"""Process factory: startup loads local models exactly once for the application."""
from archive_system.api.app import create_app

def app_factory():return create_app()
