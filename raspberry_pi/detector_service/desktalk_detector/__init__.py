"""
The detector service that runs in Docker on the Pi (README.md next to this folder says how to run it).

    settings.py      its settings, from the environment (.env through compose.yaml, and the Dockerfile)
    model_folder.py  the models folder: opens models, converting .pt to ncnn the first time
    manager.py       which model answers, loading new ones in the background, one detection at a time
    app.py           the web API: POST /detect, GET /health, PUT /model
    bootstrap.py     builds it all from the settings
    __main__.py      python -m desktalk_detector

It never imports desktalk: only desktalk_shared, FastAPI, Ultralytics and the standard library.
"""
