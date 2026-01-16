from flask import Blueprint, current_app, render_template, url_for


def create_blueprint() -> Blueprint:
    """
    cfg is the dict piece from app.config['BLUEPRINT_CONFIG']['blog']
    """
    core_bp = Blueprint(
        "Core",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    core_bp.meta = {
        "name": "System Services",
        "description": "Core services for Watchtower.",
        "image": "https://upload.wikimedia.org/wikipedia/commons/d/de/Gear-icon.png",
        "clickable": False
    }

    # -- Views ------------------------------------------------
    @core_bp.route("/")
    def index():
        modules = []
        for bp in current_app.blueprints.values():
            # Expect each BP to expose a small .meta dict
            meta = getattr(bp, "meta", {})

            # Do not render hidden blueprints
            if meta.get("hidden", False):
                continue
            # fallback route: '<bpname>.index' or blueprint's url_prefix
            if f"{bp.name}.index" in current_app.view_functions:
                target = url_for(f"{bp.name}.index")
            else:
                target = bp.url_prefix or "/"
            modules.append({
                "name": meta.get("title", bp.name.title()),
                "description": meta.get("description"),
                "image": meta.get("image"),
                "url": target,
                "clickable": meta.get("clickable", True),
            })
        return render_template("Core/index.html", modules=modules)

    return core_bp
