from web import create_app        # import your factory

app = create_app()

celery = app.extensions["celery"]