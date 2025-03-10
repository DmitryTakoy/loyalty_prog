from app import create_app

app = create_app("production")
application = app  # для gunieccorn нужна переменная application
